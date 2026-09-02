import unittest
import sys

loader = unittest.TestLoader()
suite = loader.discover('tests')
result = unittest.TextTestRunner(stream=open('test_output.log', 'w'), verbosity=0).run(suite)
with open('test_summary.txt', 'w') as f:
    f.write(f'Tests run: {result.testsRun}\n')
    f.write(f'Errors: {len(result.errors)}\n')
    f.write(f'Failures: {len(result.failures)}\n')
    f.write(f'Success: {result.wasSuccessful()}\n')
print('DONE')
