import re
from typing import Dict, Any, Tuple

class TerminalFilter:
    def __init__(self):
        # We capture context before and after error lines.
        # Common error patterns:
        self.error_patterns = [
            re.compile(r'(?i)\bError:'),
            re.compile(r'(?i)\bException:'),
            re.compile(r'(?i)\bFAIL\b'),
            re.compile(r'(?i)\bFAILED\b'),
            re.compile(r'(?i)Traceback \(most recent call last\):'),
            re.compile(r'npm ERR!'),
            re.compile(r'TSError:'),
            re.compile(r'SyntaxError:'),
            re.compile(r'TypeError:'),
            re.compile(r'ReferenceError:'),
            re.compile(r'error TS\d+:'), # Typescript errors
        ]
        
        self.ignore_patterns = [
            re.compile(r'npm WARN'),
            re.compile(r'^\s*info\s'),
            re.compile(r'^\s*debug\s'),
            re.compile(r'Download\s+\[=+\]'), # progress bars
        ]
        
        self.test_failure_indicators = [
            re.compile(r'(?i)failing tests'),
            re.compile(r'npm ERR! Test failed'),
            re.compile(r'=\s*FAILURES\s*='),
            re.compile(r'FAILED tests/'),
            re.compile(r'Errors:\s+[1-9]'),
        ]

    def filter_output(self, raw_output: str, context_lines: int = 5) -> Tuple[str, bool]:
        """
        Filters the raw terminal output.
        Returns the filtered string and a boolean indicating SUGGEST_DEBUG_LOOP.
        """
        lines = raw_output.splitlines()
        
        # Fast path: if short output, just return it.
        if len(lines) < 20:
            suggest_debug = any(
                indicator.search(raw_output) for indicator in self.test_failure_indicators
            )
            return raw_output, suggest_debug
            
        suggest_debug = False
        keep_indices = set()
        
        # Scan for errors and failures
        for i, line in enumerate(lines):
            # Check for test failures
            if not suggest_debug:
                for ind in self.test_failure_indicators:
                    if ind.search(line):
                        suggest_debug = True
                        break
            
            # Skip ignored lines eagerly if not already in a block
            ignored = False
            for ign in self.ignore_patterns:
                if ign.search(line):
                    ignored = True
                    break
            
            if ignored:
                continue
                
            is_error = False
            for pat in self.error_patterns:
                if pat.search(line):
                    is_error = True
                    break
                    
            if is_error:
                # Add context around this line
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines * 3) # include more following lines for tracebacks
                for j in range(start, end):
                    keep_indices.add(j)
                    
        if not keep_indices:
            # If no obvious errors, return the last 30 lines to give some context at least
            return "\n".join(lines[-30:]), suggest_debug
            
        filtered_lines = []
        sorted_indices = sorted(list(keep_indices))
        
        last_idx = -2
        for idx in sorted_indices:
            if idx > last_idx + 1 and last_idx != -2:
                filtered_lines.append("... [omitted lines] ...")
            filtered_lines.append(lines[idx])
            last_idx = idx
            
        return "\n".join(filtered_lines), suggest_debug
