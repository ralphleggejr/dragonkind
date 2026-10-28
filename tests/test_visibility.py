import os
import shutil
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeoutError
from dragonbot.__main__ import execute_choice


class AllOptionsVisible(unittest.TestCase):
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
        self.page.set_content('''<script>
            window.ready=true;
            window.result={selections:0,submissions:0,selectedEarly:false,submittedEarly:false};
            </script><section id="panel"><form id="choices" onsubmit="event.preventDefault();
            window.result.submissions++;window.result.submittedEarly=!window.ready;
            document.body.innerHTML='Next scene';">
            <input hidden type="radio" name="choice" id="choice-0">
            <label for="choice-0">Help your squadmate</label>
            <input hidden type="radio" name="choice" id="choice-1">
            <div id="other"><label for="choice-1">Another choice</label></div>
            </form></section><button form="choices" data-choices-submit>Next Scene</button>''')
        self.page.locator('#choice-0').evaluate("""input => input.addEventListener('change', () => {
            window.result.selections++;window.result.selectedEarly=!window.ready;
        })""")
        self.settings = patch.dict(os.environ, {'CHOICE_SUBMIT_SELECTOR': 'button[data-choices-submit]'})
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.context.close()

    def choose(self):
        execute_choice(self.page, self.page.locator('label[for="choice-0"]'))
        result = self.page.evaluate('window.result')
        self.assertEqual(result, {'selections': 1, 'submissions': 1,
                                  'selectedEarly': False, 'submittedEarly': False})

    def test_waits_for_a_surrounding_panel_animation_before_selecting(self):
        self.page.evaluate("""() => {
            window.ready=false;
            const animation=document.querySelector('#panel').animate([{opacity:.2},{opacity:1}],
                {duration:250,fill:'both'});
            animation.finished.then(()=>window.ready=true);
        }""")
        self.choose()

    def test_waits_for_an_option_hidden_by_its_container(self):
        self.page.evaluate("""() => {
            window.ready=false;document.querySelector('#other').style.contentVisibility='hidden';
            setTimeout(()=>{document.querySelector('#other').style.contentVisibility='visible';window.ready=true},150);
        }""")
        self.choose()

    def test_waits_for_every_option_again_before_next_scene(self):
        self.page.locator('#choice-0').evaluate("""input => input.addEventListener('change', () => {
            window.ready=false;document.querySelector('#other').hidden=true;
            setTimeout(()=>{document.querySelector('#other').hidden=false;window.ready=true},150);
        })""")
        self.choose()

    def test_waits_for_a_new_panel_animation_after_selection(self):
        self.page.locator('#choice-0').evaluate("""input => input.addEventListener('change', () => {
            window.ready=false;
            const animation=document.querySelector('#panel').animate([{opacity:.2},{opacity:1}],
                {duration:250,fill:'both'});
            animation.finished.then(()=>window.ready=true);
        })""")
        self.choose()

    def test_missing_option_never_selects_or_submits(self):
        self.page.set_default_timeout(200)
        self.page.locator('#other').evaluate('element=>element.hidden=true')
        with self.assertRaises(BrowserTimeoutError):
            execute_choice(self.page, self.page.locator('label[for="choice-0"]'))
        result = self.page.evaluate('window.result')
        self.assertEqual((result['selections'], result['submissions']), (0, 0))

    def test_option_hidden_after_selection_never_submits(self):
        self.page.set_default_timeout(200)
        self.page.locator('#choice-0').evaluate("""input => input.addEventListener('change', () => {
            document.querySelector('#other').hidden=true;
        })""")
        with self.assertRaises(BrowserTimeoutError):
            execute_choice(self.page, self.page.locator('label[for="choice-0"]'))
        result = self.page.evaluate('window.result')
        self.assertEqual((result['selections'], result['submissions']), (1, 0))

    def test_associated_label_outside_form_must_finish_revealing(self):
        self.page.evaluate("""() => {
            const label=document.querySelector('label[for="choice-1"]');
            document.body.append(label);label.hidden=true;window.ready=false;
            setTimeout(()=>{label.hidden=false;window.ready=true},150);
        }""")
        self.choose()


if __name__ == '__main__':
    unittest.main()
