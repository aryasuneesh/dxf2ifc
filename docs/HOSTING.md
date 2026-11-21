# Put the user-test site online (free, PythonAnywhere)

Testers get one link and a password. They see the converted plans (drawing and review list on the left,
3D model on the right), mark each item Correct, Wrong or Not sure, add comments, and press Send.
Their answers are saved in `feedback.jsonl` in your PythonAnywhere account.

The free "Beginner" account is enough: the site only serves files and saves small JSON lines.
It stays online without your laptop. Once every 3 months you press "Run until 3 months from today" on the Web tab.

## 1. Build the zip (on your laptop, 1 minute)

Convert the plans you want testers to see, then pack them:

```
python -m dxf2ifc run plan_a.dxf --out out/share/plan_a
python -m dxf2ifc run plan_b.dxf --out out/share/plan_b
python -m dxf2ifc share-pack out/share/plan_a out/share/plan_b --zip out/site.zip
```

The zip holds the pages and IFC files only (no DXF, no plan.json) and the small website file `share.py`.

Try it locally first: `python -m dxf2ifc share out/share --password test`, then open http://127.0.0.1:8000.

## 2. Create the site (10 minutes, once)

1. Sign up at pythonanywhere.com with a **Beginner** (free) account. Your site will be `https://<username>.pythonanywhere.com`.
2. **Files** tab: upload `site.zip` into your home folder `/home/<username>/`.
3. **Consoles** tab: start a **Bash** console and run:
   ```
   unzip -o site.zip
   ```
   This creates `/home/<username>/site/share.py` and `/home/<username>/site/plans/...`.
4. **Web** tab: **Add a new web app** → **Manual configuration** → any Python 3 version.
5. On the same page, open the **WSGI configuration file** link, delete everything in it, and paste
   (replace `<username>` and choose a password):
   ```python
   import sys
   sys.path.insert(0, "/home/<username>/site")
   from share import make_app
   application = make_app("/home/<username>/site/plans", password="choose-a-password")
   ```
6. Save, go back to the **Web** tab, press **Reload**. Open your site: the browser asks for a password
   (any user name, the password you chose).

## 3. Share it

Send testers the link and the password separately, e.g. the link by email and the password by phone or Teams.

## 4. Read the feedback

**Files** tab → `site/plans/feedback.jsonl` → download. Then, on your laptop:

```
python -m dxf2ifc feedback feedback.jsonl
```

This prints the number of testers, usefulness, verdicts per item, and every comment and remark.

## Updating plans

Run step 1 again, upload the new `site.zip`, `unzip -o site.zip` in the Bash console, and press **Reload**.
`feedback.jsonl` is not in the zip, so feedback already received is kept.

## Note on client data

The plans on this site are SECO's. Get SECO's agreement before uploading them, keep the password on,
and delete the site (Web tab → Delete) when the test is over.
