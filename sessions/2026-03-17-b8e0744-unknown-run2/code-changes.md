# Code Changes - b8e0744

**Commit:** b8e0744 - fix: use single quotes to avoid smart-quote encoding in save-session.ps1
**Saved at:** 2026-03-17T17:09:10.3177298-04:00

---

## Commit message

```
fix: use single quotes to avoid smart-quote encoding in save-session.ps1 
```

## Files changed

```
 scripts/save-session.ps1 | 6 ++++--  1 file changed, 4 insertions(+), 2 deletions(-)
```

## Diff (api/ ui/ scripts/)

```diff
diff --git a/scripts/save-session.ps1 b/scripts/save-session.ps1 index df30872..d42f0f8 100644 --- a/scripts/save-session.ps1 +++ b/scripts/save-session.ps1 @@ -71,10 +71,12 @@ Write-Host "  Downloading snapshot from $snapshotUrl ..." -ForegroundColor Yello  try {      Invoke-WebRequest -Uri $snapshotUrl -Headers $headers -OutFile $zipPath -UseBasicParsing      $sizeKB = [Math]::Round((Get-Item $zipPath).Length / 1024, 1) -    Write-Host ("  Download complete (" + $sizeKB + " KB)") -ForegroundColor Green +    $dlMsg = '  Download complete (' + $sizeKB + ' KB)' +    Write-Host $dlMsg -ForegroundColor Green  } catch {      Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue -    Write-Host ("  FAILED to download snapshot: " + $_) -ForegroundColor Red +    $errMsg = '  FAILED to download snapshot: ' + $_.Exception.Message +    Write-Host $errMsg -ForegroundColor Red      exit 1  }  
```

