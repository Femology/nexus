import pytest
import os
import aiosqlite
from app.services.guard import ToolGuard

@pytest.fixture
def guard(tmp_path):
    g = ToolGuard(str(tmp_path))
    # Use an in-memory or temp DB for tests
    g.ledger_db_path = os.path.join(str(tmp_path), "ledger.db")
    return g

def test_path_jail(guard, tmp_path):
    # Valid
    valid_path = guard.validate_path("src/main.py")
    assert valid_path == os.path.join(str(tmp_path), "src/main.py")
    
    # Invalid traversal
    with pytest.raises(ValueError, match="Path traversal denied"):
        guard.validate_path("../../../etc/passwd")
        
    # Edge case: absolute path outside root
    with pytest.raises(ValueError, match="Path traversal denied"):
        guard.validate_path("/etc/passwd")

@pytest.mark.asyncio
async def test_command_blocklist(guard):
    # Force trust so it doesn't fail on trust
    await guard.set_workspace_trust(True)
    
    # Test blocked commands
    blocked_commands = [
        "sudo apt install curl",
        "rm -rf /",
        "rm -rf ./node_modules",
        "sudo rm -rf /",
        "mkfs.ext4 /dev/sda1",
        "chmod -R 777 /app"
    ]
    
    for cmd in blocked_commands:
        allowed, msg = await guard.check_tool_call("run_terminal", {"command": cmd})
        assert not allowed
        assert "blocked by security policy" in msg

    # Test allowed commands
    allowed_commands = [
        "npm install",
        "ls -la",
        "cat package.json",
        "pytest tests/"
    ]
    
    for cmd in allowed_commands:
        allowed, msg = await guard.check_tool_call("run_terminal", {"command": cmd})
        assert allowed

@pytest.mark.asyncio
async def test_workspace_trust(guard):
    # Default untrusted
    allowed, msg = await guard.check_tool_call("write_file", {"path": "test.txt", "content": "hi"})
    assert not allowed
    assert "NEEDS_APPROVAL" in msg
    
    # Trusted
    await guard.set_workspace_trust(True)
    allowed, msg = await guard.check_tool_call("write_file", {"path": "test.txt", "content": "hi"})
    assert allowed
    assert msg == ""
    
    # Read tools don't need trust
    await guard.set_workspace_trust(False)
    allowed, msg = await guard.check_tool_call("read_file", {"path": "test.txt"})
    assert allowed
