from web_monitoring_diff.utils import hash_content

def test_hash_content():
    expected = 'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9'
    assert expected == hash_content(b'hello world')
