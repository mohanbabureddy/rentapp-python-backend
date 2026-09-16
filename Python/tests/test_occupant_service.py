import unittest
from unittest.mock import MagicMock, patch

from app.models import Occupant, User
from app.services import OccupantService


def _fake_file(filename="doc.pdf", mimetype="application/pdf", content_length=100, content=b"data"):
    f = MagicMock()
    f.filename = filename
    f.mimetype = mimetype
    f.content_length = content_length
    f.read.return_value = content
    return f


class OccupantServiceValidationTest(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock()
        self.user_repo = MagicMock()
        self.service = OccupantService(self.repo, self.user_repo)

    def test_missing_name_raises(self):
        with self.assertRaises(ValueError):
            self.service.add("Room1", "", _fake_file())

    def test_missing_file_raises(self):
        with self.assertRaises(ValueError):
            self.service.add("Room1", "Doc", None)

    def test_invalid_content_type_raises(self):
        with self.assertRaises(ValueError):
            self.service.add("Room1", "Doc", _fake_file(mimetype="application/zip"))

    def test_file_too_large_raises(self):
        with self.assertRaises(ValueError):
            self.service.add("Room1", "Doc", _fake_file(content_length=3 * 1024 * 1024))

    def test_tenant_not_found_raises(self):
        self.user_repo.find_by_username.return_value = None
        with self.assertRaises(ValueError):
            self.service.add("Room1", "Doc", _fake_file())


class OccupantServiceSupabaseStorageTest(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock()
        self.user_repo = MagicMock()
        self.user_repo.find_by_username.return_value = User(id=1, username="Room1", password="x")
        self.service = OccupantService(self.repo, self.user_repo)

    @patch("app.services._SUPABASE_SERVICE_KEY", "fake_key")
    @patch("app.services._SUPABASE_URL", "https://fake.supabase.co")
    @patch("app.services.requests.post")
    def test_add_uploads_to_supabase_with_forward_slash_path(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        result = self.service.add("Room1", "Aadhar Card", _fake_file(filename="scan.pdf"))

        call_args = mock_post.call_args
        self.assertIn("https://fake.supabase.co/storage/v1/object/aadhaar/Room1/", call_args[0][0])
        self.repo.save.assert_called_once()
        saved_occupant = self.repo.save.call_args[0][0]
        # Forward slashes only -- this is a URL/object-key path, not a filesystem
        # path (str(Path(...)) would use backslashes on Windows, breaking both
        # the Supabase object key and the /uploads/... URL scheme).
        self.assertNotIn("\\", saved_occupant.aadhar_storage_path)
        self.assertTrue(saved_occupant.aadhar_storage_path.startswith("aadhaar/Room1/"))
        self.assertEqual(result["tenantUsername"], "Room1")

    @patch("app.services._SUPABASE_SERVICE_KEY", "fake_key")
    @patch("app.services._SUPABASE_URL", "https://fake.supabase.co")
    @patch("app.services.requests.post")
    def test_add_raises_on_storage_failure(self, mock_post):
        mock_post.return_value = MagicMock(ok=False, status_code=500, text="error")
        with self.assertRaises(RuntimeError):
            self.service.add("Room1", "Aadhar Card", _fake_file())
        self.repo.save.assert_not_called()

    @patch("app.services._SUPABASE_SERVICE_KEY", "fake_key")
    @patch("app.services._SUPABASE_URL", "https://fake.supabase.co")
    @patch("app.services.requests.delete")
    def test_delete_removes_from_supabase_using_stripped_path(self, mock_delete):
        mock_delete.return_value = MagicMock(ok=True)
        occupant = Occupant(id=1, tenant_username="Room1", name="Aadhar", aadhar_storage_path="aadhaar/Room1/scan.pdf", verified=False)
        self.repo.find_by_id.return_value = occupant

        self.service.delete(1, ignore_verified=True)

        call_url = mock_delete.call_args[0][0]
        # bucket name ("aadhaar") must not be duplicated in the object path
        self.assertEqual(call_url, "https://fake.supabase.co/storage/v1/object/aadhaar/Room1/scan.pdf")
        self.repo.delete.assert_called_once_with(occupant)

    def test_delete_verified_occupant_rejected_without_override(self):
        occupant = Occupant(id=1, tenant_username="Room1", name="Aadhar", aadhar_storage_path=None, verified=True)
        self.repo.find_by_id.return_value = occupant
        with self.assertRaises(ValueError):
            self.service.delete(1, ignore_verified=False)
        self.repo.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
