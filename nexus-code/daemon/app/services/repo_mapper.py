import os
import networkx as nx
import tree_sitter
from tree_sitter import Language, Parser
from typing import Dict, List, Optional, Set
import asyncio
import time
import logging

try:
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    import tree_sitter_typescript as tstypescript
except ImportError:
    tspython = tsjavascript = tstypescript = None

logger = logging.getLogger(__name__)

class RepoMapper:
    def __init__(self, max_tokens: int = 2000):
        self.graph = nx.DiGraph()
        self.max_tokens = max_tokens
        
        self.parsers = {}
        if tspython and tsjavascript and tstypescript:
            self.parsers['.py'] = Parser(Language(tspython.language()))
            self.parsers['.js'] = Parser(Language(tsjavascript.language()))
            self.parsers['.ts'] = Parser(Language(tstypescript.language(), 'typescript'))
            self.parsers['.tsx'] = Parser(Language(tstypescript.language(), 'tsx'))
            
        self.file_nodes = {} # maps filename to list of node IDs
        self._last_pagerank = None
        self._last_update_time = 0
        self._recompute_needed = False
        
    def _get_parser(self, filepath: str) -> Optional[Parser]:
        _, ext = os.path.splitext(filepath)
        return self.parsers.get(ext)

    def _extract_symbols(self, filepath: str, content: str) -> Dict[str, List[str]]:
        parser = self._get_parser(filepath)
        if not parser:
            return {"classes": [], "functions": [], "imports": []}

        tree = parser.parse(content.encode('utf8'))
        root = tree.root_node

        classes = []
        functions = []
        imports = []
        
        ext = os.path.splitext(filepath)[1]
        
        if ext == '.py':
            query = Language(tspython.language()).query("""
                (class_definition name: (identifier) @class)
                (function_definition name: (identifier) @function)
                (import_statement (dotted_name) @import)
                (import_from_statement module_name: (dotted_name) @import)
            """)
        elif ext in ['.js', '.ts', '.tsx']:
            lang = tsjavascript.language() if ext == '.js' else (tstypescript.language() if ext == '.ts' else tstypescript.language())
            query = Language(lang, ext.replace('.', '') if ext in ['.ts', '.tsx'] else None).query("""
                (class_declaration name: (identifier) @class)
                (function_declaration name: (identifier) @function)
                (method_definition name: (property_identifier) @function)
                (import_statement source: (string) @import)
            """)
        else:
            return {"classes": [], "functions": [], "imports": []}

        captures = query.captures(root)
        for node, capture_name in captures:
            text = node.text.decode('utf8')
            if capture_name == 'class':
                classes.append(text)
            elif capture_name == 'function':
                functions.append(text)
            elif capture_name == 'import':
                imports.append(text)
                
        return {"classes": classes, "functions": functions, "imports": imports}

    def update_file(self, filepath: str, content: str):
        # Remove old nodes for this file
        old_nodes = self.file_nodes.get(filepath, [])
        for node in old_nodes:
            if self.graph.has_node(node):
                self.graph.remove_node(node)
                
        symbols = self._extract_symbols(filepath, content)
        
        new_nodes = []
        
        # Add file node
        self.graph.add_node(filepath, type="file")
        new_nodes.append(filepath)
        
        # Add symbol nodes
        for c in symbols["classes"]:
            node_id = f"{filepath}::{c}"
            self.graph.add_node(node_id, type="class", name=c)
            self.graph.add_edge(filepath, node_id)
            new_nodes.append(node_id)
            
        for f in symbols["functions"]:
            node_id = f"{filepath}::{f}"
            self.graph.add_node(node_id, type="function", name=f)
            self.graph.add_edge(filepath, node_id)
            new_nodes.append(node_id)
            
        # Add import edges (heuristic: link to files matching import)
        for imp in symbols["imports"]:
            # crude matching for demonstration
            imp_name = imp.strip('\'"')
            for other_file in list(self.file_nodes.keys()):
                if imp_name in other_file:
                    self.graph.add_edge(filepath, other_file)
                    
        self.file_nodes[filepath] = new_nodes
        self._recompute_needed = True
        self._last_update_time = time.time()

    def _ensure_pagerank(self):
        if not self._recompute_needed and self._last_pagerank is not None:
            return
            
        if len(self.graph) == 0:
            self._last_pagerank = {}
            self._recompute_needed = False
            return
            
        try:
            self._last_pagerank = nx.pagerank(self.graph)
        except Exception:
            self._last_pagerank = {n: 1.0 for n in self.graph.nodes()}
            
        self._recompute_needed = False

    def get_skeleton(self) -> str:
        self._ensure_pagerank()
        
        if not self._last_pagerank:
            return ""
            
        # Sort nodes by PageRank
        sorted_nodes = sorted(self._last_pagerank.items(), key=lambda x: x[1], reverse=True)
        
        lines = []
        tokens_used = 0
        
        # very crude token estimation (1 token per ~4 chars)
        for node_id, score in sorted_nodes:
            data = self.graph.nodes[node_id]
            if data.get("type") == "file":
                line = f"File: {node_id}"
            else:
                line = f"  - {data.get('type')}: {data.get('name')} (in {node_id.split('::')[0]})"
                
            est_tokens = len(line) // 4 + 1
            if tokens_used + est_tokens > self.max_tokens:
                break
                
            lines.append(line)
            tokens_used += est_tokens
            
        return "\n".join(lines)
