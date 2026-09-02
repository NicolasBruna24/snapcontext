import unittest
import sys
import subprocess

# Run tests using subprocess to avoid output mixing
result = subprocess.run(
    [sys.executable, '-m', 'unittest', 'discover', 'tests', '-v'],
    capture_output=True,
    text=True
)

# Save full output
with open('test_full_output.txt', 'w') as f:
    f.write(result.stdout)
    f.write(result.stderr)

# Parse results
lines = result.stdout.split('\n') + result.stderr.split('\n')
tests_run = 0
errors = 0
failures = 0
for line in lines:
    if 'Ran ' in line and ' tests' in line:
        tests_run = int(line.split()[1])
    if line.startswith('OK'):
        pass
    if line.startswith('FAILED'):
        parts = line.split(',')
        for p in parts:
            if 'errors=' in p:
                errors = int(p.split('=')[1])
            if 'failures=' in p:
                failures = int(p.split('=')[1])

with open('final_summary.txt', 'w') as f:
    f.write(f'Tests run: {tests_run}\n')
    f.write(f'Errors: {errors}\n')
    f.write(f'Failures: {failures}\n')
    f.write(f'Exit code: {result.returncode}\n')
    f.write(f'Success: {result.returncode == 0}\n')

print(f'Tests run: {tests_run}, Errors: {errors}, Failures: {failures}, Exit: {result.returncode}')
