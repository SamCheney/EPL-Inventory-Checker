from app.updater import UpdateChecker

checker = UpdateChecker()
update = checker.check()

if update is None:
    print("Already up to date.")
else:
    print(f"Latest version : {update.latest_version}")
    print(f"Release URL    : {update.release_url}")
    print(f"Download URL   : {update.download_url}")