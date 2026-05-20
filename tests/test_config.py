import json
import os
import pickle
import tempfile
import unittest

from ricoh_config import load_printers, save_printers


class ConfigTests(unittest.TestCase):
    def test_save_and_load_printers_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'printers.json')
            save_printers([{
                'IP': '10.0.0.5',
                'Name': 'Test Printer',
                'Serial': '',
                'EID': '',
                'Default': 'True',
            }], path)

            printers = load_printers(path)

        self.assertEqual(printers[0]['IP'], '10.0.0.5')
        self.assertTrue(printers[0]['Default'])

    def test_legacy_pickle_migrates_to_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, 'printers.json')
            pickle_path = os.path.join(tmpdir, 'Printers.pkl')
            with open(pickle_path, 'wb') as f:
                pickle.dump([{
                    'IP': '10.0.0.5',
                    'Name': 'Test Printer',
                    'Serial': '',
                    'EID': '',
                    'Default': True,
                }], f)

            printers = load_printers(json_path, pickle_path)

            self.assertTrue(os.path.exists(json_path))
            with open(json_path, 'r') as f:
                saved = json.load(f)

        self.assertEqual(printers[0]['Name'], 'Test Printer')
        self.assertEqual(saved[0]['Name'], 'Test Printer')

    def test_malformed_json_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'printers.json')
            with open(path, 'w') as f:
                f.write('{bad json')

            with self.assertRaises(ValueError):
                load_printers(path)


if __name__ == '__main__':
    unittest.main()
