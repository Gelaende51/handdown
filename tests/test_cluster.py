import numpy as np

from handdown.cluster import group
from handdown.metrics import measure

NS = 'xmlns="http://www.w3.org/2000/svg"'


def feat(body: str) -> np.ndarray:
    return np.asarray(measure(f'<svg {NS} viewBox="0 0 24 24">{body}</svg>')["feature"], dtype=np.float16)


def test_same_depiction_different_style_groups_together_and_apart_from_other_depiction():
    can_filled = feat('<path d="M6 7h12l-1 14H7z"/><rect x="4" y="4" width="16" height="2"/>')
    can_outline = feat('<path fill="none" stroke="#000" stroke-width="2" d="M6 7h12l-1 14H7z"/><rect x="4" y="4" width="16" height="2"/>')
    x_mark = feat('<path fill="none" stroke="#000" stroke-width="3" d="M5 5L19 19M19 5L5 19"/>')
    x_thin = feat('<path fill="none" stroke="#000" stroke-width="2" d="M5 5L19 19M19 5L5 19"/>')
    labels = group(np.stack([can_filled, can_outline, x_mark, x_thin]))
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert labels[0] == labels[1]
