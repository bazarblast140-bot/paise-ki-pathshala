from realchart import capture


class FakePage:
    def __init__(self):
        self.calls = []

    def wait_for_selector(self, *a, **k):
        raise TimeoutError

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, *args):
        self.calls.append(js)
        if js == capture.HIDE_ALL_JS:
            return "missing"  # view-only daily tab: no drawing toolbar
        if js == capture.HIDE_ALL_API_JS:
            return "hidden"
        raise AssertionError(js)


def test_hide_drawings_falls_back_to_widget_toggle_without_removing():
    page = FakePage()
    assert capture.hide_drawings(page) is True
    assert capture.HIDE_ALL_API_JS in page.calls
    assert "removeAllDrawingTools" not in capture.HIDE_ALL_API_JS
    assert "save(" not in capture.HIDE_ALL_API_JS.lower()
