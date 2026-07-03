class EditFormatter:
    @staticmethod
    def wrap_system_prompt_for_edit(base_prompt: str) -> str:
        return f"""{base_prompt}

You must format your code modifications as search/replace blocks.
Use the following format strictly:

<<<<
[exact old code to be replaced]
====
[new code to replace it with]
>>>>

The old code block must match exactly what is in the file, including leading spaces.
Do not use this format if you are just answering a question or if you don't need to change a file.
"""
