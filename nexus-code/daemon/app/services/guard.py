import os
import re
import aiosqlite
from typing import Dict, Any, Tuple

class ToolGuard:
    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self.ledger_db_path = os.path.expanduser("~/.nexus-code/ledger.db")
        
        # Dangerous commands that are always blocked
        self.command_blocklist = [
            re.compile(r'(?i)^\s*sudo\b'),
            re.compile(r'(?i)\brm\s+-r'),
            re.compile(r'(?i)\bmkfs\b'),
            re.compile(r'(?i)\bdd\b'),
            re.compile(r'(?i)\bchmod\s+-R\s+777\b'),
            re.compile(r'(?i)\bchown\s+-R\b'),
            re.compile(r'(?i)>\s*/dev/sd[a-z]'),
        ]

    async def _init_trust_table(self):
        os.makedirs(os.path.dirname(self.ledger_db_path), exist_ok=True)
        async with aiosqlite.connect(self.ledger_db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS workspace_trust (
                    workspace_path TEXT PRIMARY KEY,
                    is_trusted INTEGER
                )
            ''')
            await db.commit()

    async def is_workspace_trusted(self) -> bool:
        await self._init_trust_table()
        async with aiosqlite.connect(self.ledger_db_path) as db:
            async with db.execute("SELECT is_trusted FROM workspace_trust WHERE workspace_path = ?", (self.workspace_root,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0] == 1:
                    return True
        return False

    async def set_workspace_trust(self, trusted: bool):
        await self._init_trust_table()
        async with aiosqlite.connect(self.ledger_db_path) as db:
            await db.execute('''
                INSERT INTO workspace_trust (workspace_path, is_trusted)
                VALUES (?, ?)
                ON CONFLICT(workspace_path) DO UPDATE SET is_trusted = excluded.is_trusted
            ''', (self.workspace_root, 1 if trusted else 0))
            await db.commit()

    def validate_path(self, target_path: str) -> str:
        """
        Validates that target_path is within workspace_root.
        Returns absolute normalized path if valid, raises ValueError if not.
        """
        if not target_path:
            raise ValueError("Path argument cannot be empty.")
            
        abs_path = os.path.abspath(os.path.join(self.workspace_root, target_path))
        
        # Ensure the resolved path starts with the workspace root to prevent directory traversal
        if not abs_path.startswith(self.workspace_root + os.sep) and abs_path != self.workspace_root:
            raise ValueError(f"Path traversal denied: {target_path} resolves outside workspace root.")
            
        return abs_path

    async def check_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates the tool call.
        Returns (is_allowed, error_message_if_blocked)
        If allowed but untrusted, throws a custom exception or returns specific flag.
        Here we assume the caller handles untrusted via the return value or exceptions.
        """
        # 1. Path validation
        if 'path' in arguments:
            try:
                self.validate_path(arguments['path'])
            except ValueError as e:
                return False, str(e)
                
        # 2. Command validation
        if tool_name == 'run_terminal' and 'command' in arguments:
            cmd = arguments['command']
            for pattern in self.command_blocklist:
                if pattern.search(cmd):
                    return False, f"Command blocked by security policy: matched pattern {pattern.pattern}"
                    
        # 3. Workspace Trust validation
        # Only require trust for modifying commands
        modifying_tools = {'write_file', 'create_file', 'run_terminal', 'apply_edit'}
        if tool_name in modifying_tools:
            is_trusted = await self.is_workspace_trusted()
            if not is_trusted:
                return False, "NEEDS_APPROVAL: Workspace is untrusted. Please approve this action manually."
                
        return True, ""
