import pytest
import networkx as nx
from app.services.repo_mapper import RepoMapper

@pytest.fixture
def mapper():
    return RepoMapper(max_tokens=100)

def test_extract_symbols(mapper):
    # This requires tree-sitter to be installed properly, which we gracefully handle if it's missing in CI
    if not mapper.parsers:
        pytest.skip("Tree-sitter parsers not installed")
        
    content = """
class MyClass:
    def my_method(self):
        pass

def standalone_func():
    pass

import os
from datetime import datetime
    """
    
    symbols = mapper._extract_symbols("test.py", content)
    assert "MyClass" in symbols["classes"]
    assert "standalone_func" in symbols["functions"]
    assert "os" in symbols["imports"]

def test_update_file_and_pagerank(mapper):
    content1 = "class A: pass"
    content2 = "class B: pass\nimport A"
    
    mapper.update_file("file1.py", content1)
    mapper.update_file("file2.py", content2)
    
    assert "file1.py" in mapper.graph
    assert "file2.py" in mapper.graph
    
    skel = mapper.get_skeleton()
    assert "File: file1.py" in skel or "File: file2.py" in skel
    assert "class: A" in skel or "class: B" in skel

def test_incremental_update_drops_old_nodes(mapper):
    mapper.update_file("file1.py", "class A: pass")
    assert "file1.py::A" in mapper.graph
    
    # Update removes class A
    mapper.update_file("file1.py", "class B: pass")
    assert "file1.py::A" not in mapper.graph
    assert "file1.py::B" in mapper.graph
