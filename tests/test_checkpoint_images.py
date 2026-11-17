import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import smtplib
import struct
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from dragonbot.checkpoint_images import (MAX_IMAGE_BYTES, PNG_HEADER, RETENTION_SECONDS,
    ScreenshotEvidenceError, image_root, prune_checkpoint_images, read_checkpoint_png,
    rotation_screenshots, save_checkpoint_png)
from dragonbot.__main__ import (capture_paused_tree_screenshot, check_tree_checkpoint,
    deliver_tree_reports, execute_choice, finish_rotation_report, TreeCheckpointReview)
from dragonbot.core import load_state
from dragonbot.rotation import begin_rotation, record_result

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
ACCOUNTS = json.loads(Path('route.json').read_text())['accounts']


def snapshot():
    return {'options': [{'control': 'choice-0', 'text': 'Climb down slowly to pursue the green'},
                        {'control': 'choice-1', 'text': 'Descend quickly'}],
            'visible_count': 2, 'all_visible': True, 'animations_complete': True}


class ImageStorage(unittest.TestCase):
    def test_png_is_atomic_immutable_and_uses_a_portable_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            evidence = save_checkpoint_png(state_path, 'a', PNG, snapshot(), 'before selection')
            self.assertEqual(read_checkpoint_png(evidence, image_root(state_path)), PNG)
            self.assertEqual(evidence['file'], Path(evidence['file']).name)
            self.assertEqual(evidence['sha256'], hashlib.sha256(PNG).hexdigest())
            self.assertEqual(len(list(image_root(state_path).iterdir())), 1)

    def test_changed_or_outside_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            evidence = save_checkpoint_png(state_path, 'a', PNG, snapshot(), 'before selection')
            for filename in ('../.env', '/tmp/outside.png', None):
                with self.assertRaises(ScreenshotEvidenceError):
                    read_checkpoint_png({**evidence, 'file': filename}, image_root(state_path))
            (image_root(state_path) / evidence['file']).write_bytes(PNG + b'changed')
            with self.assertRaises(ScreenshotEvidenceError):
                read_checkpoint_png(evidence, image_root(state_path))

    def test_oversized_capture_is_not_saved_or_passed_off_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            with self.assertRaises(ScreenshotEvidenceError):
                save_checkpoint_png(state_path, 'a', PNG + bytes(MAX_IMAGE_BYTES), snapshot(), 'before selection')
            self.assertFalse(image_root(state_path).exists())

    def test_retention_preserves_queued_active_and_round_images(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            images = [save_checkpoint_png(state_path, str(i), PNG, snapshot(), 'before selection') for i in range(4)]
            root = image_root(state_path)
            for image in images:
                os.utime(root / image['file'], (0, 0))
            manual = root / 'my-manual-image.png'
            manual.write_bytes(PNG)
            os.utime(manual, (0, 0))
            state = {'accounts': {'a': {'tree_checkpoint_screenshot': images[0]}},
                     'tree_report_outbox': [{'attachments': [images[1]]}],
                     'reporting_rotation': {'results': {'a': [{'tree_checkpoint_screenshot': images[2]}]}}}
            prune_checkpoint_images(state, root, now=RETENTION_SECONDS + 10)
            self.assertTrue(all((root / image['file']).exists() for image in images[:3]))
            self.assertFalse((root / images[3]['file']).exists())
            self.assertTrue(manual.exists())

    def test_selection_uses_latest_current_round_image_and_never_a_skipped_account(self):
        rotation = {'accounts': ['a', 'b', 'c'], 'results': {
            'a': [{'attempted': True, 'tree_checkpoint_screenshot': {'file': 'old', 'account': 'a'}},
                  {'attempted': True}, {'attempted': True, 'tree_checkpoint_screenshot': {'file': 'latest', 'account': 'a'}}],
            'b': [{'attempted': False, 'tree_checkpoint_screenshot': {'file': 'prior-round', 'account': 'b'}}]}}
        attachments, missing = rotation_screenshots(rotation)
        self.assertEqual([image['file'] for image in attachments], ['latest'])
        self.assertEqual(missing, ['b', 'c'])


class EmailScreenshots(unittest.TestCase):
    def make_round(self, state_path):
        state = {'accounts': {}, 'global_pause': False}
        for account in ACCOUNTS:
            evidence = save_checkpoint_png(state_path, account, PNG, snapshot(), 'before selection')
            evidence['verified_after_capture'] = True
            state['accounts'][account] = {'status': 'cooldown', 'next_run': 20000,
                                           'tree_checkpoint_screenshot': evidence}
        begin_rotation(state, ACCOUNTS)
        for account in ACCOUNTS:
            record_result(state, ACCOUNTS, account)
        return state

    def test_final_email_contains_all_eight_named_png_attachments(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            state = self.make_round(state_path)
            with patch('dragonbot.__main__.STATE', state_path), \
                 patch.dict(os.environ, {'MAIL_USER': 'inbox@example.com', 'MAIL_PASSWORD': 'fixture-only'}), \
                 patch('dragonbot.mail.smtplib.SMTP') as smtp:
                finish_rotation_report(state)
                deliver_tree_reports(state)
                message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
                self.assertEqual(message['To'], 'rljr.code@gmail.com')
                attachments = list(message.iter_attachments())
                self.assertEqual(len(attachments), 8)
                self.assertEqual(len({image.get_filename() for image in attachments}), 8)
                self.assertTrue(all(image.get_content_type() == 'image/png' for image in attachments))
                self.assertTrue(all(image.get_payload(decode=True) == PNG for image in attachments))
                self.assertIn('Checkpoint screenshots: 8 of 8', message.get_body(preferencelist=('plain',)).get_content())
                smtp.return_value.__enter__.return_value.send_message.assert_called_once()

    def test_queued_images_survive_restart_and_project_move_before_delivery_retry(self):
        with tempfile.TemporaryDirectory() as original, tempfile.TemporaryDirectory() as moved:
            state_path = Path(original) / 'state.json'
            state = self.make_round(state_path)
            with patch('dragonbot.__main__.STATE', state_path), \
                 patch('dragonbot.__main__.time.time', return_value=1000), \
                 patch('dragonbot.__main__.notify', side_effect=smtplib.SMTPServerDisconnected('fixture')):
                finish_rotation_report(state)
                deliver_tree_reports(state)
            saved = load_state(state_path)
            new_state_path = Path(moved) / 'state.json'
            shutil.copytree(image_root(state_path), image_root(new_state_path))
            new_state_path.write_text(json.dumps(saved))
            with patch('dragonbot.__main__.STATE', new_state_path), \
                 patch('dragonbot.__main__.time.time', return_value=1300), \
                 patch.dict(os.environ, {'MAIL_USER': 'inbox@example.com', 'MAIL_PASSWORD': 'fixture-only'}), \
                 patch('dragonbot.mail.smtplib.SMTP') as smtp:
                deliver_tree_reports(saved)
                message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
                self.assertEqual(len(list(message.iter_attachments())), 8)
                self.assertFalse(load_state(new_state_path)['tree_report_outbox'])

    def test_missing_image_is_reported_as_an_error_with_the_other_seven_attached(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            state = self.make_round(state_path)
            first = state['accounts'][ACCOUNTS[0]]['tree_checkpoint_screenshot']
            (image_root(state_path) / first['file']).unlink()
            with patch('dragonbot.__main__.STATE', state_path), \
                 patch.dict(os.environ, {'MAIL_USER': 'inbox@example.com', 'MAIL_PASSWORD': 'fixture-only'}), \
                 patch('dragonbot.mail.smtplib.SMTP') as smtp:
                finish_rotation_report(state)
                deliver_tree_reports(state)
                message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
                self.assertIn('SCREENSHOT ERROR', message['Subject'])
                self.assertEqual(len(list(message.iter_attachments())), 7)
                text = message.get_body(preferencelist=('plain',)).get_content()
                self.assertIn('these images were not attached', text)
                self.assertIn('Checkpoint screenshots: 7 of 8', text)
                self.assertIn(ACCOUNTS[0], text)


class ScreenshotTiming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = sync_playwright().start()
        executable = os.environ.get('TEST_CHROMIUM_PATH') or shutil.which('chromium')
        try:
            cls.browser = cls.driver.chromium.launch(executable_path=executable, headless=True,
                                                   args=['--disable-dev-shm-usage'])
        except Exception:
            cls.driver.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.driver.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_default_timeout(1500)
        self.directory = tempfile.TemporaryDirectory()
        self.state_path = Path(self.directory.name) / 'state.json'
        self.state = {'accounts': {'a': {'status': 'active'}}, 'global_pause': False}
        self.page.set_content('''<script>window.selections=0;window.submissions=0</script>
            <p>Account details outside the options</p><div style="height:120px"></div>
            <form id="choices" onsubmit="event.preventDefault();window.submissions++;document.body.innerHTML='Next scene'">
            <input hidden type="radio" name="choice" id="choice-0" onchange="window.selections++">
            <label for="choice-0">Climb down slowly to pursue the green</label>
            <input hidden type="radio" name="choice" id="choice-1">
            <label for="choice-1">Descend quickly in preparation for whatever’s coming your way</label>
            </form><button form="choices" data-choices-submit>Next Scene</button>''')
        self.settings = patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'CHOICES_SELECTOR': 'label[for^="choice-"]',
                                               'CHOICE_SUBMIT_SELECTOR': 'button[data-choices-submit]'})
        self.settings.start()
        self.path_patch = patch('dragonbot.__main__.STATE', self.state_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.settings.stop()
        self.context.close()
        self.directory.cleanup()

    def prepare(self):
        return check_tree_checkpoint(self.page, self.state, 'a', 'initial')[1]

    def test_real_options_png_is_captured_before_any_radio_is_changed(self):
        expected = self.prepare()
        check_tree_checkpoint(self.page, self.state, 'a', 'before selection', expected)
        evidence = self.state['accounts']['a']['tree_checkpoint_screenshot']
        data = read_checkpoint_png(evidence, image_root(self.state_path))
        width, height = struct.unpack('>II', data[16:24])
        self.assertGreater(width, 200)
        self.assertGreater(height, 15)
        self.assertTrue(evidence['all_visible'])
        self.assertTrue(evidence['verified_after_capture'])
        self.assertEqual(evidence['option_count'], 2)
        self.assertEqual(self.page.evaluate('[window.selections,window.submissions]'), [0, 0])
        self.assertGreater(evidence['capture_rect']['y'], self.page.locator('p').bounding_box()['y'] + 30)

    def test_waits_for_delayed_option_and_never_fast_forwards_its_animation(self):
        self.page.locator('label[for="choice-1"]').evaluate('''element=>{
            window.revealDone=false;
            element.animate([{opacity:0},{opacity:1}],{duration:300,fill:'both'}).finished.then(()=>window.revealDone=true);
        }''')
        expected = self.prepare()
        original = self.page.screenshot
        def photograph(**kwargs):
            self.assertTrue(self.page.evaluate('window.revealDone'))
            self.assertEqual(kwargs['animations'], 'allow')
            return original(**kwargs)
        with patch.object(self.page, 'screenshot', side_effect=photograph):
            check_tree_checkpoint(self.page, self.state, 'a', 'before selection', expected)
        self.assertTrue(self.state['accounts']['a']['tree_checkpoint_screenshot']['verified_after_capture'])

    def test_screenshot_failure_prevents_selection_and_next_scene(self):
        expected = self.prepare()
        with patch.object(self.page, 'screenshot', side_effect=RuntimeError('fixture image error')):
            with self.assertRaises(ScreenshotEvidenceError):
                execute_choice(self.page, self.page.locator('label[for="choice-0"]'),
                    before_action=lambda stage: check_tree_checkpoint(self.page, self.state, 'a', stage, expected))
        self.assertEqual(self.page.evaluate('[window.selections,window.submissions]'), [0, 0])

    def test_shadow_inserted_during_capture_is_caught_before_selection(self):
        expected = self.prepare()
        original = self.page.screenshot
        def photograph(**kwargs):
            data = original(**kwargs)
            self.page.locator('#choices').evaluate('''element=>element.insertAdjacentHTML('beforeend',
                '<input hidden type=radio name=choice id=choice-2><label for=choice-2>Follow the shadow</label>')''')
            return data
        with patch.object(self.page, 'screenshot', side_effect=photograph):
            with self.assertRaises(TreeCheckpointReview):
                execute_choice(self.page, self.page.locator('label[for="choice-0"]'),
                    before_action=lambda stage: check_tree_checkpoint(self.page, self.state, 'a', stage, expected))
        self.assertEqual(self.page.evaluate('[window.selections,window.submissions]'), [0, 0])
        self.assertTrue(self.state['accounts']['a']['tree_checkpoint_checks'][-1]['shadow_found'])

    def test_paused_shadow_is_photographed_after_its_natural_reveal(self):
        self.page.locator('#choices').evaluate('''element=>{
            element.insertAdjacentHTML('beforeend','<input hidden type=radio name=choice id=choice-2><label hidden for=choice-2>Follow the shadow</label>');
            setTimeout(()=>document.querySelector('label[for="choice-2"]').hidden=false,120);
        }''')
        with self.assertRaises(TreeCheckpointReview):
            self.prepare()
        capture_paused_tree_screenshot(self.page, self.state, 'a')
        evidence = self.state['accounts']['a']['tree_checkpoint_screenshot']
        self.assertEqual(evidence['option_count'], 3)
        self.assertTrue(evidence['all_visible'])
        self.assertTrue(evidence['verified_after_capture'])
        self.assertEqual(self.page.evaluate('[window.selections,window.submissions]'), [0, 0])

    def test_never_revealed_shadow_is_labelled_unfinished_not_forced_visible(self):
        self.page.set_default_timeout(200)
        self.page.locator('#choices').evaluate('''element=>element.insertAdjacentHTML('beforeend',
            '<input hidden type=radio name=choice id=choice-2><label hidden for=choice-2>Follow the shadow</label>')''')
        with self.assertRaises(TreeCheckpointReview):
            self.prepare()
        capture_paused_tree_screenshot(self.page, self.state, 'a')
        evidence = self.state['accounts']['a']['tree_checkpoint_screenshot']
        self.assertEqual(evidence['option_count'], 3)
        self.assertEqual(evidence['visible_count'], 2)
        self.assertFalse(evidence['all_visible'])
        self.assertFalse(evidence['verified_after_capture'])
        self.assertFalse(self.page.locator('label[for="choice-2"]').is_visible())


if __name__ == '__main__':
    unittest.main()
