import unittest

from app.database import check_environment, is_local_db

HOSTED = "postgresql+psycopg://user:pw@db.example.supabase.com:5432/postgres"
LOCAL_MYSQL = "mysql+pymysql://root:pw@localhost:3306/rent_app"
SQLITE = "sqlite:///tenant_billing.db"


class IsLocalDbTest(unittest.TestCase):
    def test_local_forms(self):
        for url in (LOCAL_MYSQL, SQLITE, "mysql+pymysql://root:pw@127.0.0.1/rent_app"):
            self.assertTrue(is_local_db(url), url)

    def test_hosted_is_not_local(self):
        self.assertFalse(is_local_db(HOSTED))


class CheckEnvironmentTest(unittest.TestCase):
    def test_local_refuses_a_hosted_database(self):
        with self.assertRaises(RuntimeError) as ctx:
            check_environment("local", HOSTED)
        self.assertNotIn("pw", str(ctx.exception))  # never echoes credentials

    def test_local_accepts_local_databases(self):
        check_environment("local", LOCAL_MYSQL)
        check_environment("LOCAL", SQLITE)

    def test_production_refuses_local_or_missing_database(self):
        for url in (LOCAL_MYSQL, SQLITE):
            with self.assertRaises(RuntimeError):
                check_environment("production", url)

    def test_production_accepts_the_hosted_database(self):
        check_environment("production", HOSTED)

    def test_unset_enforces_nothing(self):
        for url in (HOSTED, LOCAL_MYSQL, SQLITE):
            check_environment("", url)
            check_environment(None, url)


if __name__ == "__main__":
    unittest.main()
