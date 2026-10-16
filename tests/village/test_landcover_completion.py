"""Completion changes unknown imagery hypotheses, never fixed seeds or NoData."""

import numpy as np
import pytest

from pyfireca.village.imagery_landcover import classify_rgb_candidates, complete_rgb_candidates


def test_dark_green_shadow_can_be_inferred_without_assigning_inert_ground():
    pytest.importorskip("cv2")
    rgb = np.array([[[6]], [[12]], [[5]]], dtype=np.uint8)
    valid = np.ones((1, 1), dtype=bool)
    raw = classify_rgb_candidates(rgb, valid)
    assert raw[0, 0] == 0
    result, reason, report = complete_rgb_candidates(rgb, raw, valid)
    assert result[0, 0] == 1
    assert reason[0, 0] == 1
    assert report["final_unknown_cell_count"] == 0


def test_context_completes_shadow_but_preserves_seeds_and_nodata():
    pytest.importorskip("cv2")
    rgb = np.zeros((3, 15, 30), dtype=np.uint8)
    rgb[:, :, :20] = np.array([30, 90, 20])[:, None, None]
    rgb[:, :, 20:] = 140
    valid = np.ones((15, 30), dtype=bool)
    valid[0, 0] = False
    rgb[:, 6:9, 7:10] = 10  # neutral shadow inside surrounding vegetation
    raw = classify_rgb_candidates(rgb, valid)
    known = valid & (raw != 0)
    result, reason, report = complete_rgb_candidates(rgb, raw, valid)
    np.testing.assert_array_equal(result[known], raw[known])
    assert np.all(result[6:9, 7:10] == 1)
    assert result[0, 0] == 255 and reason[0, 0] == 255
    assert report["contextual_cell_count"] == 9
    assert report["accuracy_validated"] is False


def test_featureless_image_without_context_does_not_invent_a_material():
    pytest.importorskip("cv2")
    rgb = np.full((3, 8, 8), 10, dtype=np.uint8)
    with pytest.raises(ValueError, match="No classified neighbours"):
        complete_rgb_candidates(rgb, np.zeros((8, 8), dtype=np.uint8), np.ones((8, 8), dtype=bool))
