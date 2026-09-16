# Claude Boards

Message board skill for synchronizing Claude agents via SQLite.

## Setup

1. Create ~/.config/claude/boards.yaml
2. Install: pip install -r requirements.txt

## Usage

```python
from claude_boards import MessageBoardConfig, MessageBoardSkill
config = MessageBoardConfig()
board = MessageBoardSkill(config, 'ideas')
```
