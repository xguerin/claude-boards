import yaml
import os

class MessageBoardConfig:
    def __init__(self, config_file=None):
        if config_file is None:
            config_file = os.environ.get('BOARD_CONFIG', os.path.expanduser('~/.config/claude/boards.yaml'))
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Config file not found: {config_file}")
        with open(config_file) as f:
            config = yaml.safe_load(f)['board']
        self.location = config['location']
        self.boards_dir = config['boards_dir']
        if self.location == 'ssh':
            if not all(k in config for k in ['host', 'user', 'port', 'key_path']):
                raise ValueError("SSH config missing required fields")
        self.host = config.get('host')
        self.user = config.get('user')
        self.port = config.get('port')
        self.key_path = config.get('key_path')
        self.poll_interval = config.get('poll_interval', 10)
