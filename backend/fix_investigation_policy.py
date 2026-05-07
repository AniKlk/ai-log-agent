#!/usr/bin/env python3
"""
Fix escaped quotes in investigation_policy.py
"""
import re

file_path = 'app/agent/investigation_policy.py'

# Read the file
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the specific problematic line by removing all backslash-escapes
# Pattern: (\\"...\\"
pattern = r'\\\"'
replacement = '"'

content = re.sub(pattern, replacement, content)

# Write back
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Fixed: {file_path}")
