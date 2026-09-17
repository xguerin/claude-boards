import argparse
import time

from .config import MessageBoardConfig
from .skill import MessageBoardSkill


def watch(board_name, agent_id, interval=None):
    """Poll a board forever, printing (and acking) each new message as it arrives.

    Each message is printed as a header line followed by its content
    verbatim — newlines are preserved (code, lists), and the Monitor tool
    batches lines emitted together into a single notification, so one
    message still arrives as one event.
    """
    config = MessageBoardConfig()
    if interval is None:
        interval = config.poll_interval
    board = MessageBoardSkill(config, board_name, agent_id)
    while True:
        for mid, sender, receiver, content, topic, ts, created in reversed(board.get_new_messages(limit=50)):
            suffix = f' (topic: {topic})' if topic else ''
            print(f'[{ts}] {sender} -> {receiver}{suffix}:\n{content}', flush=True)
            board.acknowledge_message(mid)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description='Poll a claude_boards board for new messages.')
    parser.add_argument('board_name')
    parser.add_argument('agent_id')
    parser.add_argument('--interval', type=float, default=None,
                         help='Poll interval in seconds (default: poll_interval from config, else 10)')
    args = parser.parse_args()
    watch(args.board_name, args.agent_id, args.interval)


if __name__ == '__main__':
    main()
