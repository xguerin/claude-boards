from .config import MessageBoardConfig
from .skill import MessageBoardSkill

_board = None

def init_board(board_name: str, agent_id: str) -> str:
    """Open a board as `agent_id`. All later sends/reads/acks use this identity."""
    global _board
    config = MessageBoardConfig()
    _board = MessageBoardSkill(config, board_name, agent_id)
    return f"Board '{board_name}' initialized as '{agent_id}'"

def post_message(content: str, receiver_id: str = 'all', topic: str = None) -> int:
    """Post a message to the board as the agent passed to init_board()"""
    return _board.post_message(content, receiver_id, topic)

def get_messages(limit: int = 10) -> list:
    """Get new messages for the agent passed to init_board()"""
    return _board.get_new_messages(limit)

def ack_message(message_id: int) -> None:
    """Mark message as read for the agent passed to init_board()"""
    return _board.acknowledge_message(message_id)
