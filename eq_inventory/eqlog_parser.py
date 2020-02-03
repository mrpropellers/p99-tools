import os
import csv
from datetime import datetime
from configparser import ConfigParser
import pickle
import re
import logging
# Enabling this will cause the parser to throw out previous history and re-parse the entire log
# it loads, rather than starting from where it believes it left off last time
_DEBUG = False
_LOG = logging.getLogger()
logging.basicConfig()
_LOG.setLevel(logging.DEBUG if _DEBUG else logging.INFO)

# NOTE: Not actually writing the config out right now - just change values in dict below
_CONFIG_FILE = "config.cfg"
_TRADE_REGEX = re.compile(r"(?P<PLAYER>\w+) has offered you a (?P<ITEM>\w+)[.]")

# TODO: Config interactions should all be handled in their own script
_CONFIG_DEFAULTS = {
    "eq_path": "C:\\Program Files (x86)\\Sony\\EverQuest\\Logs",
    "mule_names": "Libarian,Mule,Freeport,Devook",
    "word_inventory": "./words.csv",
    "item_inventory": "./items.csv",
    "inventory_pickle": "./history.pickle",
    "log_name": "eqlog_<NAME>_P1999Teal.txt"
}

# Patterns to match to determine if item is for word inventory or item inventory
_WORD_PATTERNS = {
    re.compile(r"Part of Tasarin's Grimoire \w+"),
    re.compile(r"Velishoul's Tome Pg. \w+"),
    re.compile(r"Rune of \w+"),
    re.compile(r"Spell: \w+"),
    re.compile(r"Words of \w+")
}


def check_for_trade_action(line: str):
    return re.match(_TRADE_REGEX, line[27:])


# TODO: Organize some classes into utility scripts
def get_log_timestamp(log_line: str) -> datetime:
    try:
        return datetime.strptime(log_line[:26], "[%a %b %d %H:%M:%S %Y]")
    except ValueError as e:
        if log_line != "\n":
            _LOG.warning("Failed to find timestamp for: '%s'" % log_line)


def make_log_path(eq_path: str, log_name: str, character_name: str) -> str:
    log_name_parts = log_name.split("<NAME>")
    full_log_name = log_name_parts[0] + character_name + log_name_parts[1]
    return os.path.join(eq_path, full_log_name)


class History:
    def __init__(self):
        self.timestamps = [datetime(1900, 1, 1)]
        self.actions = [""]

    def record(self, timestamp: datetime, action):
        self.timestamps.append(timestamp)
        self.actions.append("%s -> %s" % (action.group("PLAYER"), action.group("ITEM")))


class Inventory:
    _word_name_column = 2
    _word_count_column = 3
    _item_name_column = 1
    _item_count_column = 2

    def __init__(self, config_path:str):
        self.config = ConfigParser()
        if not os.path.exists(config_file) or _DEBUG:
            _LOG.warning("No config found at %s - creating new one." % config_file)
            self.config["DEFAULT"] = _CONFIG_DEFAULTS
            #with open(_CONFIG_FILE, 'w') as f:
            #    self.config.write(f)
        else:
            self.config.read(config_file)
        self.history_path = self.config["DEFAULT"]["inventory_pickle"]
        if os.path.exists(self.history_path) and not _DEBUG:
            with open(self.history_path, 'rb') as f:
                self.history = pickle.load(f)
        else:
            _LOG.warning("No parsing history found at %s - creating new one." % self.history_path)
            self.history = History()
        self.words = {}
        self.items = {}

    def update(self):
        eq_path = self.config["DEFAULT"]["eq_path"]
        log_name = self.config["DEFAULT"]["log_name"]
        mule_names = self.config["DEFAULT"]["mule_names"].split(",")
        last_action_time = self.history.timestamps[-1]
        num_trades_processed = 0

        for mule_name in mule_names:
            num_trades_for_mule = 0
            log_path = make_log_path(eq_path, log_name, mule_name)
            if not os.path.exists(log_path):
                _LOG.warning("Can't find log file for %s at %s" % (mule_name, log_path))
                continue
            _LOG.info("Processing %s's log file..." % mule_name)
            with open(log_path, 'r') as f:
                for line in f.readlines():
                    timestamp = get_log_timestamp(line)
                    if timestamp is None or timestamp <= last_action_time:
                        continue
                    match = check_for_trade_action(line)
                    if match:
                        self.process_trade(timestamp, match)
                        num_trades_processed += 1
                        num_trades_for_mule += 1
            _LOG.info("%d new trades found for %s" % (num_trades_for_mule, mule_name))
        _LOG.info("%d total new trades found since %s" % (num_trades_processed, last_action_time))
        self.write_new_trades()

    def process_trade(self, timestamp: datetime, trade: re.Match):
        self.history.record(timestamp, trade)
        item_name = trade.group("ITEM")
        if any(re.match(pattern, item_name) for pattern in _WORD_PATTERNS) or item_name in self.words:
            add_to = self.words
            _LOG.info("Adding %s to words inventory." % item_name)
        else:
            add_to = self.items
            _LOG.info("Adding %s to items inventory" % item_name)

        if item_name in add_to:
            add_to[item_name] = add_to[item_name] + 1
        else:
            add_to[item_name] = 1
        _LOG.info("%s count is now %d" % (item_name, add_to[item_name]))

    def add_counts_to_csv(self, csv_path, name_column, count_column, new_counts):
        out_file_name = csv_path[:-4] + "_updated.csv"
        in_file = open(csv_path, 'r', newline="")
        reader = csv.reader(in_file)
        out_file = open(out_file_name, 'w', newline="")
        writer = csv.writer(out_file)
        num_columns = 0
        for row in reader:
            num_columns = max(len(row), num_columns)
            if len(row) <= max(name_column, count_column):
                _LOG.warning("'%s' has too few columns - can't read it." % ",".join(row))
                continue
            item_name = row[name_column]
            if item_name in new_counts:
                _LOG.info("Found %s is csv. Adding %d to count." % (item_name, new_counts[item_name]))
                # Counts will get removed from the dict as they get added to rows
                row[count_column] = str(int(row[count_column]) + new_counts.pop(item_name))
            writer.writerow(row)
        # Add any counts that weren't already in the csv
        for name, count in new_counts.items():
            new_row = [None] * num_columns
            new_row[name_column] = name
            new_row[count_column] = count
            _LOG.info("Didn't find any entries for %s in csv. Adding %d to the bottom." % (name, count))
            writer.writerow(new_row)
        in_file.close()
        out_file.close()
        os.remove(csv_path)
        os.rename(out_file_name, csv_path)

    def write_new_trades(self):
        with open(self.history_path, 'wb') as f:
            pickle.dump(self.history, f)
        if any(self.words):
            words_path = self.config['DEFAULT']['word_inventory']
            self.add_counts_to_csv(words_path, self._word_name_column, self._word_count_column,
                                   self.words)
        if any(self.items):
            items_path = self.config['DEFAULT']['item_inventory']
            self.add_counts_to_csv(items_path, self._item_name_column, self._item_count_column,
                                   self.items)


if __name__ == '__main__':
    this_dir = os.path.dirname(os.path.realpath(__file__))
    config_file = os.path.join(this_dir, _CONFIG_FILE)
    inventory_parser = Inventory(config_file)
    inventory_parser.update()
