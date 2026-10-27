import unittest

from button_exposure import ExposureButtons


class ExposureButtonsTests(unittest.TestCase):
    def controller(self, value=1):
        hardware = {'value': value, 'writes': []}
        def write(value):
            hardware['writes'].append(value)
            hardware['value'] = value
        return ExposureButtons(lambda: hardware['value'], write, 1, 10000), hardware

    def press(self, controller, mask, start):
        controller.poll(mask, start)
        return controller.poll(mask, start + .06)

    def test_press_steps_one_unit_and_hold_does_not_repeat(self):
        controller, hardware = self.controller()
        self.assertEqual(self.press(controller, 2, 0), 2)
        controller.poll(2, 3)
        self.assertEqual(hardware['writes'], [2])
        self.press(controller, 0, 4)
        self.assertEqual(self.press(controller, 1, 5), 1)

    def test_bounds_and_simultaneous_keys_do_not_write(self):
        controller, hardware = self.controller()
        self.press(controller, 1, 0)
        self.press(controller, 0, 1)
        self.press(controller, 3, 2)
        self.assertEqual(hardware['writes'], [])
        controller, hardware = self.controller(10000)
        self.press(controller, 2, 0)
        self.assertEqual(hardware['writes'], [])

    def test_bounce_is_ignored_and_readback_mismatch_fails(self):
        controller, hardware = self.controller()
        controller.poll(2, 0)
        controller.poll(0, .01)
        controller.poll(2, .02)
        controller.poll(0, .03)
        controller.poll(0, .1)
        self.assertEqual(hardware['writes'], [])
        controller = ExposureButtons(lambda: 1, lambda value: None, 1, 10000)
        with self.assertRaises(RuntimeError):
            self.press(controller, 2, 0)

    def test_simultaneous_keys_require_full_release(self):
        controller, hardware = self.controller(10)
        self.press(controller, 3, 0)
        self.press(controller, 2, 1)
        self.assertEqual(hardware['writes'], [])
        self.press(controller, 0, 2)
        hardware['value'] = 20
        self.assertEqual(self.press(controller, 2, 3), 21)


if __name__ == '__main__':
    unittest.main()
