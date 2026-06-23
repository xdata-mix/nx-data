cd C:\Users\guill\Downloads
if (Test-Path nx-data-bulk) { Remove-Item nx-data-bulk -Recurse -Force }
git clone https://github.com/xdata-mix/nx-data.git nx-data-bulk
cd nx-data-bulk

# 5 scripts
Copy-Item ..\nxdata_split\refresh_tf1.py        scripts\refresh_tf1.py
Copy-Item ..\nxdata_split\refresh_m6.py         scripts\refresh_m6.py
Copy-Item ..\nxdata_split\refresh_francetv.py   scripts\refresh_francetv.py
Copy-Item ..\nxdata_split\refresh_arte.py       scripts\refresh_arte.py
Copy-Item ..\nxdata_split\combine_replays.py    scripts\combine_replays.py

# 5 workflows
Copy-Item ..\nxdata_split\refresh_bfm.yml       .github\workflows\refresh_bfm.yml
Copy-Item ..\nxdata_split\refresh_tf1.yml       .github\workflows\refresh_tf1.yml
Copy-Item ..\nxdata_split\refresh_m6.yml        .github\workflows\refresh_m6.yml
Copy-Item ..\nxdata_split\refresh_francetv.yml  .github\workflows\refresh_francetv.yml
Copy-Item ..\nxdata_split\refresh_arte.yml      .github\workflows\refresh_arte.yml

git config user.name "guil"
git config user.email "linkinep61@gmail.com"
git add -A
git commit -m "Split scrapers: 4 scripts/site + 5 workflows cron + combine_replays"
git push origin main
