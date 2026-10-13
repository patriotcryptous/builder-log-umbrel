# Builder Log

A self-hosted build log for Van's Aircraft kits — and any other kit plane. Log
your shop sessions, track completion per kit, annotate photos, check parts off
the packing list, record costs, and print a builder log for inspection day. Your
data stays on your own server.

Free and open source, and built to cover what the paid and hosted builder-log
tools do, without a subscription and without your build living on someone
else's machine.

## Features

- **Build log** — hours, notes, tags and photos per session, with contributor
  hours so a helper's work is on the record. A work timer runs while you build,
  and a quick-add page suits a phone in the shop.
- **Completion tracking** — a percentage per kit and per section, with weights
  for the bigger jobs, or a manual percentage for a whole kit.
- **Photos** — captions, a searchable gallery and numbered annotations you place
  by clicking the photo.
- **Inventory** — packing lists with received, partial, backordered, missing and
  damaged states, CSV import, and where each part lives in the shop.
- **Costs** — by category and kit, against an optional budget, with receipts
  attached and cost per build hour.
- **Documents** — plans, invoices, service bulletins, weight and balance sheets
  and paperwork you own, all in one place.
- **Insights** — hours by month, cumulative hours, hours per kit and per person,
  the days you actually build, longest sessions, and a finish estimate from your
  own pace.
- **Search** — one box across log entries, photo captions and pins, documents,
  parts and costs.
- **Exports** — log CSV, inventory CSV, costs CSV, everything as JSON, a photo
  archive with captions and annotations, a full backup zip, and a printable
  builder log PDF in Letter or A4.
- **Imports** — a CSV from the EAA Builders Log, MyKitLog, KitFlight or a
  spreadsheet, and restore from a backup zip.

## Install on umbrelOS

1. Open the Umbrel App Store, then **Community App Stores** (the three dots,
   top right).
2. Add this store:

   ```
   https://github.com/patriotcryptous/builder-log-umbrel
   ```

3. Open the **Hangar** store and install **Builder Log**.
4. Open the app and set your project details under **Settings** — model, kit
   number, planned hours and budget.

Updates keep your data. Only uninstalling removes it.

## Run it without Umbrel

```bash
git clone https://github.com/patriotcryptous/builder-log-umbrel
cd builder-log-umbrel
pip install -r requirements.txt
HBL_DATA_DIR=./data python -m uvicorn app.main:app --port 8080
```

Then open <http://localhost:8080>. Tests run with `python -m pytest tests/ -q`.

## Your data

Everything lives in the app's data folder: a SQLite database, your photos and
your documents. **Nothing in this app contacts the internet** — no analytics, no
update checks, no outbound calls of any kind. Take a backup from
**Settings → Full backup ZIP** before you upgrade anything, and keep a copy off
the server.

## Support

Open an issue at
<https://github.com/patriotcryptous/builder-log-umbrel/issues>.

## Disclaimer

Not affiliated with, endorsed by, or connected to Van's Aircraft, Inc., the EAA,
KitFlight, MyKitLog or any other builder-log product. "RV-12iS", "RV-14" and
other model names are used only to describe which aircraft a build record is
for. You are responsible for your own build records and for meeting the
requirements of your aviation authority; this app is a convenience, not a
compliance tool. It ships no manufacturer plans, instructions or checklists —
upload the documents you own.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built by Wyley Gump with Claude.
