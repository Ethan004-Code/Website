"""Exercise the supplied SQL on a disposable local PostgreSQL cluster.

Uses no app credentials, does not touch Supabase or the Replit database, exposes
no network listener, and removes its own temporary cluster on completion.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).parent
ENV = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}


def main():
    with tempfile.TemporaryDirectory(prefix="echopantry-sql-") as folder:
        data = str(Path(folder) / "db")
        subprocess.run(["initdb", "-D", data, "-U", "ep_verify", "--auth=trust",
                        "--no-locale", "--encoding=UTF8"], env=ENV,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        started = False
        try:
            subprocess.run(["pg_ctl", "-D", data, "-l", str(Path(folder) / "postgres.log"),
                            "-o", f"-h '' -k {folder} -p 55439", "-w", "start"], env=ENV,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=30)
            started = True
            command = ["psql", "-X", "-h", folder, "-p", "55439", "-U", "ep_verify",
                       "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-Atq"]

            def sql(text, valid=True):
                result = subprocess.run(command, input=text, text=True, capture_output=True, env=ENV, timeout=20)
                if valid and result.returncode:
                    raise RuntimeError(result.stderr)
                return result

            sql("create role anon; create role authenticated; create role service_role bypassrls;"
                "create schema auth; create table auth.users(id uuid primary key);")
            sql((HERE / "supabase_schema.sql").read_text())
            sql((HERE / "interview_migration.sql").read_text())
            sql((HERE / "interview_migration.sql").read_text())
            print("PASS: original schema and additive migration, including rerun")
            owner = "00000000-0000-0000-0000-000000000001"
            lid = "00000000-0000-0000-0000-000000000002"
            sql(f"insert into auth.users values ('{owner}');"
                f"insert into user_profiles(id,display_name) values ('{owner}','SQL check');")
            row = dict(id=lid, user_id=owner, name="SQL check", mode="giveaway",
                       listing_type="single", items=["Onions"], quantities={"Onions": 3},
                       deadline="2099-01-02T00:00:00Z", location="Library steps",
                       wanted_items=[],
                       availability="Exact window", item_details=[dict(name="Onions", quantity=3,
                       unit="each", category="produce")], pickup_start="2099-01-01T10:00:00Z",
                       pickup_end="2099-01-01T11:00:00Z", status="open", is_test=True)
            payload = json.dumps([dict(table="listings", row=row)]).replace("'", "''")
            sql(f"set role service_role; select echopantry_commit(0,'{payload}'::jsonb);")
            assert sql("select status from listings;").stdout.strip() == "open"
            assert sql("select wanted_items::text from listings;").stdout.strip() == "[]"
            assert sql("select revision from echopantry_revision;").stdout.strip() == "1"
            assert sql("set role service_role; select echopantry_snapshot()->>'schema_version';").stdout.strip() == "4"
            print("PASS: service-role transaction inserts real structured listing")

            row["status"] = "reserved"
            payload = json.dumps([dict(table="listings", row=row)]).replace("'", "''")
            sql(f"set role service_role; select echopantry_commit(1,'{payload}'::jsonb);")
            assert sql("select status from listings;").stdout.strip() == "reserved"
            print("PASS: transaction updates full listing snapshot")

            # An error in a later write must also undo the first write.
            row["status"] = "open"
            payload = json.dumps([dict(table="listings", row=row),
                                 dict(table="listings", row={"id": "00000000-0000-0000-0000-000000000003"})]).replace("'", "''")
            result = sql(f"set role service_role; select echopantry_commit(2,'{payload}'::jsonb);", valid=False)
            assert result.returncode != 0
            assert sql("select status from listings;").stdout.strip() == "reserved"
            assert sql("select revision from echopantry_revision;").stdout.strip() == "2"
            print("PASS: multirow failure rolls back all writes and revision")

            def compete():
                return sql("set role service_role; select echopantry_commit(2,'[]'::jsonb);", valid=False)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                a, b = list(pool.map(lambda _: compete(), range(2)))
            assert sum(r.returncode == 0 for r in (a, b)) == 1
            assert "EP_CONFLICT" in (a.stderr + b.stderr)
            print("PASS: competing transactions have exactly one winner")
            assert sql("select has_function_privilege('anon','echopantry_snapshot()','EXECUTE');").stdout.strip() == "f"
            assert sql("select has_function_privilege('authenticated','echopantry_commit(bigint,jsonb)','EXECUTE');").stdout.strip() == "f"
            snapshot = json.loads(sql("set role service_role; select echopantry_snapshot();").stdout.strip())
            assert len(snapshot["listings"]) == 1 and snapshot["revision"] == 3
            print("PASS: browser roles denied direct transaction/snapshot access")
            print("PASS: consistent service-role snapshot returns committed revision")
            approval_id = "00000000-0000-0000-0000-000000000010"
            approval = dict(id=approval_id, user_id=owner, listing_digest="d" * 64,
                            photo_id="00000000-0000-0000-0000-000000000011",
                            photo_sha256="a" * 64, policy="listing-photo-v1",
                            approved_at="2099-01-01T00:00:00Z", expires_at="2099-01-01T00:15:00Z",
                            used_listing_id=None)
            photo = dict(id=approval["photo_id"], user_id=owner, sha256=approval["photo_sha256"],
                         state="active", created_at=approval["approved_at"])
            payload = json.dumps([dict(table="listing_photos", row=photo),
                                  dict(table="listing_approvals", row=approval)])
            sql(f"set role service_role; select echopantry_commit(3,'{payload}'::jsonb);")
            approval["used_listing_id"] = lid
            row.update(photo_id=approval["photo_id"], photo_sha256=approval["photo_sha256"],
                       bob_review_id=approval_id, bob_reviewed_at=approval["approved_at"])
            payload = json.dumps([dict(table="listing_approvals", row=approval), dict(table="listings", row=row)])
            sql(f"set role service_role; select echopantry_commit(4,'{payload}'::jsonb);")
            assert sql("select used_listing_id from listing_approvals;").stdout.strip() == lid
            assert sql("select bob_review_id from listings;").stdout.strip() == approval_id
            assert sql("select has_table_privilege('anon','listing_approvals','SELECT');").stdout.strip() == "f"
            print("PASS: approval creation, consumption and photo fields commit atomically; receipts are private")
            duplicate = dict(row, id="00000000-0000-0000-0000-000000000099")
            payload = json.dumps([dict(table="listings", row=duplicate)])
            assert sql(f"set role service_role; select echopantry_commit(5,'{payload}'::jsonb);", valid=False).returncode != 0
            print("PASS: duplicate approval identity cannot publish another listing")
        finally:
            if started:
                subprocess.run(["pg_ctl", "-D", data, "-m", "immediate", "stop"], env=ENV,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


if __name__ == "__main__":
    main()
