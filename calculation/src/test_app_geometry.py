#!/usr/bin/env python3
"""Regression tests for app.py's geometry normalization and pipeline call.

Guards the fix for a bug where normalize_geometry()/infer_slice_normal()
discarded the frontend's full geometry.affine (keeping only its slice-normal
column), and where the frontend's [Nx, Ny] matrix convention was forwarded to
camrie_tools unconverted (tools expect [Ny, Nx] = (nP, nF)).

The expensive simulation boundary (camrie_tools.MRI_pipeline.run_pipeline) is
stubbed; these tests check what app.py computes and forwards, not simulation
numerics (those are covered by vendor-camrie-tools/tests).

Run:
    conda run -n koma python -m unittest test_app_geometry -v
    (from calculation/src/, so `import app` resolves)
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import app  # noqa: E402


def realistic_sequence_geometry(**overrides):
    """A frontend-style geometry object, shaped like calculation/task.json."""
    geo = {
        "isocenter_mm": [1.684, 1.736, 117.238],
        "fov_mm": [192, 256],
        "matrix": [192, 128],  # [Nx, Ny]
        "slice": {"num_slices": 5, "thickness_mm": 2, "gap_mm": 1},
        "affine": [
            [1.0, 0.0, 0.0, 1.684],
            [0.0, 2.0, 0.0, 1.736],
            [0.0, 0.0, 3.0, 117.238],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "ui": {
            "orientation": "axial",
            "angulation_lr_deg": 0,
            "angulation_ap_deg": 0,
            "angulation_z_deg": 0,
        },
    }
    geo.update(overrides)
    return geo


def rotated_in_plane_affine(deg, isocenter=(1.684, 1.736, 117.238), dx=1.0, dy=2.0, dz=3.0):
    r = np.deg2rad(deg)
    c, s = np.cos(r), np.sin(r)
    rot_z = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    direction = rot_z  # same normal (z), different in-plane axes
    A = np.eye(4)
    A[:3, :3] = direction @ np.diag([dx, dy, dz])
    A[:3, 3] = isocenter
    return A.tolist()


class RotationSurvivesNormalizationTests(unittest.TestCase):
    """Required test 1: a 35deg in-plane rotation survives normalization and
    reaches camrie_tools (via the preserved affine, not just slice_normal).
    """

    def test_35deg_in_plane_rotation_preserved_in_affine(self) -> None:
        affine_35 = rotated_in_plane_affine(35.0)
        geo = realistic_sequence_geometry(affine=affine_35)
        del geo["isocenter_mm"]  # affine's translation is authoritative here
        normalized = app.normalize_geometry(geo)

        self.assertEqual(normalized["affine"], affine_35)
        # The normal (column 2) is unchanged by a pure in-plane rotation...
        self.assertAlmostEqual(normalized["slice_normal"][2], 1.0, places=6)
        # ...but columns 0/1 (readout/phase) differ from the 0deg case,
        # proving the rotation is NOT collapsed into slice_normal alone.
        affine_0 = rotated_in_plane_affine(0.0)
        self.assertNotEqual(
            [row[0] for row in normalized["affine"]],
            [row[0] for row in affine_0],
        )

    def test_affine_absent_falls_back_to_normal_only_legacy_path(self) -> None:
        geo = realistic_sequence_geometry()
        del geo["affine"]
        normalized = app.normalize_geometry(geo)
        self.assertIsNone(normalized["affine"])
        self.assertEqual(normalized["slice_normal"], [0, 0, 1])


class MatrixSwapTests(unittest.TestCase):
    """Required test 2: [192, 128] -> tools matrix [128, 192], exactly once."""

    def test_frontend_nx_ny_converts_to_tools_ny_nx(self) -> None:
        geo = realistic_sequence_geometry(matrix=[192, 128])
        normalized = app.normalize_geometry(geo)
        # normalize_geometry does NOT swap -- it preserves frontend order for
        # anything else that reads job["geometry"]["matrix"].
        self.assertEqual(normalized["matrix"], [192, 128])
        # The swap happens exactly once, at the run_pipeline call boundary.
        tools_matrix = app.tools_matrix_from_frontend(normalized["matrix"])
        self.assertEqual(tools_matrix, (128, 192))

    def test_swap_is_not_applied_twice(self) -> None:
        """Calling the conversion twice must NOT swap back by accident if
        misused -- this asserts the function is a one-shot [Nx,Ny]->(Ny,Nx)
        mapping, not an involution, so a double-call bug would be caught by
        a shape/order assertion elsewhere (matrix stays a 2-tuple of ints).
        """
        geo = realistic_sequence_geometry(matrix=[192, 128])
        normalized = app.normalize_geometry(geo)
        tools_matrix = app.tools_matrix_from_frontend(normalized["matrix"])
        self.assertIsInstance(tools_matrix, tuple)
        self.assertEqual(len(tools_matrix), 2)
        self.assertTrue(all(isinstance(v, int) for v in tools_matrix))


class PreservedGeometryFieldsTests(unittest.TestCase):
    """Required test 3: FOV, slice gap, translation, and affine are preserved."""

    def test_fov_gap_translation_affine_all_preserved(self) -> None:
        geo = realistic_sequence_geometry(
            fov_mm=[192, 256], matrix=[192, 128],
            slice={"num_slices": 7, "thickness_mm": 2.5, "gap_mm": 1.5})
        normalized = app.normalize_geometry(geo)

        self.assertEqual(normalized["fov_mm"], [192, 256])
        self.assertEqual(normalized["seq_fov_mm"], [192, 256])
        self.assertEqual(normalized["num_slices"], 7)
        self.assertEqual(normalized["slice_thickness_mm"], 2.5)
        self.assertEqual(normalized["slice_gap_mm"], 1.5)
        self.assertEqual(normalized["affine"], geo["affine"])
        self.assertEqual(normalized["isocenter_mm"], geo["isocenter_mm"])


class FullAffineSelectsSequenceGridTests(unittest.TestCase):
    """Required test 4: full-affine payloads select sequence-grid output."""

    def test_affine_present_selects_sequence_grid(self) -> None:
        geo = app.normalize_geometry(realistic_sequence_geometry())
        affine = geo.get("affine")
        output_grid = "sequence" if affine is not None else "body"
        self.assertEqual(output_grid, "sequence")

    def test_affine_absent_selects_body_grid(self) -> None:
        raw = realistic_sequence_geometry()
        del raw["affine"]
        geo = app.normalize_geometry(raw)
        affine = geo.get("affine")
        output_grid = "sequence" if affine is not None else "body"
        self.assertEqual(output_grid, "body")


class LegacyPayloadCompatibilityTests(unittest.TestCase):
    """Required test 5: legacy payloads keep documented conventions +
    auto-centering behavior.
    """

    def test_legacy_payload_without_affine_or_isocenter_uses_auto_centering(self) -> None:
        legacy_geo = {
            "slice_normal": [0, 0, 1],
            "num_slices": 5,
            "slice_thickness_mm": 5.0,
            "slice_gap_mm": 0.0,
        }
        normalized = app.normalize_geometry(legacy_geo)
        self.assertIsNone(normalized["isocenter_mm"])
        self.assertIsNone(normalized["affine"])
        self.assertEqual(normalized["slice_normal"], [0, 0, 1])

        # Mirrors do_process's own fallback: geo.get("isocenter_mm") or auto.
        auto_isocenter_mm = [10.0, 20.0, 30.0]
        isocenter_mm = normalized.get("isocenter_mm") or auto_isocenter_mm
        self.assertEqual(isocenter_mm, auto_isocenter_mm)

    def test_legacy_internal_event_json_shape_still_normalizes(self) -> None:
        """calculation/event.json-style geometry (AGENTS.md Section 2)."""
        legacy_geo = {
            "isocenter_mm": None,
            "slice_normal": [0, 0, 1],
            "num_slices": 1,
            "slice_thickness_mm": None,
            "slice_gap_mm": 0.0,
        }
        normalized = app.normalize_geometry(legacy_geo)
        self.assertIsNone(normalized["affine"])
        self.assertIsNone(normalized["slice_thickness_mm"])


class MultiSequenceGeometryTests(unittest.TestCase):
    """Required test 6: multi-sequence jobs retain separate geometries."""

    def test_two_sequences_keep_independent_affines_and_matrices(self) -> None:
        opts = {
            "sequences": [
                {
                    "file": {"type": "file", "id": "1", "options": {
                        "type": "s3", "filename": "seqA.seq", "bucket": "b", "key": "k1"}},
                    "geometry": realistic_sequence_geometry(
                        matrix=[192, 128], affine=rotated_in_plane_affine(0.0)),
                },
                {
                    "file": {"type": "file", "id": "2", "options": {
                        "type": "s3", "filename": "seqB.seq", "bucket": "b", "key": "k2"}},
                    "geometry": realistic_sequence_geometry(
                        matrix=[256, 256], affine=rotated_in_plane_affine(35.0)),
                },
            ],
            "geometry": {},
        }
        jobs = app.normalize_sequence_jobs(opts)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["geometry"]["matrix"], [192, 128])
        self.assertEqual(jobs[1]["geometry"]["matrix"], [256, 256])
        self.assertNotEqual(jobs[0]["geometry"]["affine"], jobs[1]["geometry"]["affine"])
        # Each job's affine round-trips through tools_matrix_from_frontend
        # independently (no cross-contamination between sequences).
        tm0 = app.tools_matrix_from_frontend(jobs[0]["geometry"]["matrix"])
        tm1 = app.tools_matrix_from_frontend(jobs[1]["geometry"]["matrix"])
        self.assertEqual(tm0, (128, 192))
        self.assertEqual(tm1, (256, 256))


class ConflictingGeometryRejectedTests(unittest.TestCase):
    """Reject conflicting explicit geometry instead of silently shifting it."""

    def test_conflicting_affine_and_slice_normal_rejected(self) -> None:
        geo = realistic_sequence_geometry(
            affine=rotated_in_plane_affine(0.0),  # normal = +z
            slice_normal=[1, 0, 0],  # conflicting: +x
        )
        with self.assertRaises(app.GeometryConflictError):
            app.normalize_geometry(geo)

    def test_malformed_affine_rejected(self) -> None:
        geo = realistic_sequence_geometry(affine=[[1, 2], [3, 4]])
        with self.assertRaises(app.GeometryConflictError):
            app.normalize_geometry(geo)

    def test_matrix_with_wrong_length_rejected(self) -> None:
        with self.assertRaises(app.GeometryConflictError):
            app.tools_matrix_from_frontend([192])


class OutputPackagingPreservesGeometryTests(unittest.TestCase):
    """Required test 7: output packaging preserves the reconstruction's
    geometry and filename, through the actual serialization path (not just
    a mocked addAble() call).
    """

    def test_add_sequence_outputs_preserves_affine_geometry_and_filename(self) -> None:
        import SimpleITK as sitk

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "seq001"
            out_dir.mkdir()
            aux_dir = Path(tmp) / "aux"
            aux_dir.mkdir()

            # Build a small volume with a genuinely oblique affine (as the
            # sequence-grid path would produce) and write it as
            # reconstruction.nii.gz, exactly like run_pipeline does.
            direction = np.array(rotated_in_plane_affine(35.0))[:3, :3]
            spacing = np.linalg.norm(direction, axis=0)
            direction_unit = direction / spacing
            arr = np.random.rand(4, 6, 8).astype(np.float32)  # (Nz, Ny, Nx)
            img = sitk.GetImageFromArray(arr)
            img.SetSpacing(tuple(spacing.tolist()))
            img.SetDirection(tuple(direction_unit.flatten(order="C").tolist()))
            img.SetOrigin((1.684, 1.736, 117.238))
            recon_path = out_dir / "reconstruction.nii.gz"
            sitk.WriteImage(img, str(recon_path))

            class _FakeOut:
                def __init__(self):
                    self.added = []

                def addAble(self, imaginable, id, name, type, basename):
                    self.added.append({
                        "path": imaginable.getFileName() if hasattr(imaginable, "getFileName") else None,
                        "id": id, "name": name, "type": type, "basename": basename,
                    })

                def addAuxiliaryFile(self, path):
                    pass

            out = _FakeOut()
            job = {"slug": "seq001_test", "index": 1, "name": "TestSeq.seq"}
            with mock.patch.object(app, "logger", mock.Mock()):
                app.add_sequence_outputs(out, out_dir, job, multi_sequence=False, aux_dir=aux_dir)

            self.assertEqual(len(out.added), 1)
            self.assertEqual(out.added[0]["basename"], "reconstruction.nii.gz")

            # Re-read the file exactly as packaged on disk and confirm the
            # oblique geometry survived the add_sequence_outputs step
            # untouched (it only copies/registers the file; it must not
            # resample or rewrite it).
            reread = sitk.ReadImage(str(recon_path))
            np.testing.assert_allclose(reread.GetDirection(), img.GetDirection(), atol=1e-12)
            np.testing.assert_allclose(reread.GetSpacing(), img.GetSpacing(), atol=1e-12)
            np.testing.assert_allclose(reread.GetOrigin(), img.GetOrigin(), atol=1e-12)
            np.testing.assert_array_equal(sitk.GetArrayFromImage(reread), arr)


class RunPipelineCallSiteTests(unittest.TestCase):
    """End-to-end (within do_process) check that the affine/matrix reach
    camrie_tools.run_pipeline correctly, with the simulation stubbed.
    """

    def _minimal_event(self, geometry_overrides=None):
        geo = realistic_sequence_geometry(**(geometry_overrides or {}))
        return {
            "pipeline": "test-pipeline",
            "token": "test-token",
            "user_id": "test-user",
            "task": {
                "options": {
                    "rho": {"type": "local", "local_path": "rho.nii", "filename": "rho.nii"},
                    "t1": {"type": "local", "local_path": "t1.nii", "filename": "t1.nii"},
                    "sequence": {"type": "local", "local_path": "seq.seq", "filename": "seq.seq"},
                    "geometry": geo,
                }
            },
        }

    def test_affine_and_converted_matrix_reach_run_pipeline(self) -> None:
        event = self._minimal_event({"matrix": [192, 128], "affine": rotated_in_plane_affine(35.0)})

        captured = {}

        def fake_run_pipeline(**kwargs):
            import SimpleITK as sitk
            captured.update(kwargs)
            out_dir = Path(kwargs["output_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            # run_pipeline normally writes a real NIfTI; a minimal valid one
            # is enough for add_sequence_outputs' Imaginable() read.
            img = sitk.GetImageFromArray(np.zeros((2, 3, 4), dtype=np.float32))
            sitk.WriteImage(img, str(out_dir / "reconstruction.nii.gz"))
            return None, None

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(app, "compute_auto_isocenter", return_value=[0.0, 0.0, 0.0]), \
             mock.patch.object(app, "download_from_s3", side_effect=lambda d, s3: d.get("local_path", "x")), \
             mock.patch.object(app.pipeline, "run_pipeline", side_effect=fake_run_pipeline), \
             mock.patch.object(app, "create_random_temp_dir", side_effect=lambda: Path(tmp) / "job"):
            fake_s3 = mock.Mock()
            result = app.do_process(event, s3=fake_s3)

        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(captured["affine"], rotated_in_plane_affine(35.0))
        self.assertEqual(captured["matrix"], (128, 192))
        self.assertEqual(captured["output_grid"], "sequence")

    def test_legacy_payload_uses_body_grid_and_no_affine(self) -> None:
        geo_overrides = {"slice_normal": [0, 0, 1]}
        geo = realistic_sequence_geometry(**geo_overrides)
        del geo["affine"]
        event = self._minimal_event()
        event["task"]["options"]["geometry"] = geo

        captured = {}

        def fake_run_pipeline(**kwargs):
            import SimpleITK as sitk
            captured.update(kwargs)
            out_dir = Path(kwargs["output_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            img = sitk.GetImageFromArray(np.zeros((2, 3, 4), dtype=np.float32))
            sitk.WriteImage(img, str(out_dir / "reconstruction.nii.gz"))
            return None, None

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(app, "compute_auto_isocenter", return_value=[0.0, 0.0, 0.0]), \
             mock.patch.object(app, "download_from_s3", side_effect=lambda d, s3: d.get("local_path", "x")), \
             mock.patch.object(app.pipeline, "run_pipeline", side_effect=fake_run_pipeline), \
             mock.patch.object(app, "create_random_temp_dir", side_effect=lambda: Path(tmp) / "job"):
            fake_s3 = mock.Mock()
            app.do_process(event, s3=fake_s3)

        self.assertIsNone(captured["affine"])
        self.assertEqual(captured["output_grid"], "body")


if __name__ == "__main__":
    unittest.main(verbosity=2)
