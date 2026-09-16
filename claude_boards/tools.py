from .config import MessageBoardConfig
from .skill import MessageBoardSkill

_board = None

def init_board(board_name: str):
    """Initialize a message board"""
    global _board
    config = MessageBoardConfig()
    _board = MessageBoardSkill(config, board_name)
    return f"Board '{board_name}' initialized"

def post_message(agent_id: str, content: str, receiver_id: str = 'all', topic: str = None) -> int:
    """Post a message to the board"""
    return _board.post_message(agent_id, content, receiver_id, topic)

def get_messages(agent_id: str, limit: int = 10) -> list:
    """Get new messages for this agent"""
    return _board.get_new_messages(agent_id, limit)

def ack_message(agent_id: str, message_id: int) -> None:
    """Mark message as read"""
    return _board.acknowledge_message(agent_id, message_id)
