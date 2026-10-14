$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$projectRoot = Get-FreshLinkRoot
$baseUrl = 'http://localhost:18000'
$work = Join-Path $projectRoot 'work'
if (-not (Test-Path -LiteralPath $work)) { New-Item -ItemType Directory -Path $work | Out-Null }
$resultPath = Join-Path $work ('a01-permissions-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.json')
$result = @{stage='A01 live ordinary-user permissions';status='failed';checks=@();test_user_cleanup='not_created';error=$null}
$createdUser = $null
$userHeaders = $null
$testEmail = 'a01-check-' + [Guid]::NewGuid().ToString('N') + '@example.com'
# Signup does not send email in the existing template; use a disposable local user.
try {
    $passwordLine = Get-Content -LiteralPath (Join-Path $projectRoot '.env.freshlink') | Where-Object { $_ -match '^FIRST_SUPERUSER_PASSWORD=' } | Select-Object -First 1
    if (-not $passwordLine) { throw 'Local admin password missing.' }
    $adminToken = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/login/access-token" -ContentType 'application/x-www-form-urlencoded' -Body @{username='admin@example.com';password=$passwordLine.Substring($passwordLine.IndexOf('=')+1)} -TimeoutSec 20
    $adminHeaders = @{Authorization=('Bearer ' + $adminToken.access_token)}
    $adminPage = Invoke-RestMethod -Uri "$baseUrl/api/v1/devices/?limit=1" -Headers $adminHeaders -TimeoutSec 20
    if ($adminPage.data.Count -lt 1) { throw 'Run the device HTTP check first.' }
    $deviceId = $adminPage.data[0].id
    $testPassword = New-FreshLinkSecret
    $createdUser = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/users/signup" -ContentType 'application/json' -Body (@{email=$testEmail;password=$testPassword;full_name='A01 disposable permission check'} | ConvertTo-Json -Compress) -TimeoutSec 20
    $result.test_user_cleanup = 'pending'
    if ($createdUser.is_superuser -or $createdUser.email -ne $testEmail) { throw 'Unexpected test user properties.' }
    $userToken = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/login/access-token" -ContentType 'application/x-www-form-urlencoded' -Body @{username=$testEmail;password=$testPassword} -TimeoutSec 20
    $userHeaders = @{Authorization=('Bearer ' + $userToken.access_token)}
    $userPage = Invoke-RestMethod -Uri "$baseUrl/api/v1/devices/?limit=1" -Headers $userHeaders -TimeoutSec 20
    if ($userPage.count -ne $adminPage.count) { throw 'Ordinary user list count differs.' }
    $detail = Invoke-RestMethod -Uri "$baseUrl/api/v1/devices/$deviceId" -Headers $userHeaders -TimeoutSec 20
    if ($detail.id -ne $deviceId) { throw 'Ordinary user cannot read device detail.' }
    $result.checks += 'ordinary_user_list_and_detail'
    Write-Output 'PASS ordinary user can read device list and detail'
    $statusCode = 0
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$baseUrl/api/v1/devices/" -Headers $userHeaders -ContentType 'application/json' -Body (@{code=('denied-' + [Guid]::NewGuid().ToString('N'));name='Must not be registered';kind='temperature_sensor'} | ConvertTo-Json -Compress) -TimeoutSec 20
        $statusCode = [int]$response.StatusCode
    } catch { if ($null -eq $_.Exception.Response) { throw }; $statusCode = [int]$_.Exception.Response.StatusCode }
    if ($statusCode -ne 403) { throw "Ordinary-user registration must return 403, got $statusCode" }
    $after = Invoke-RestMethod -Uri "$baseUrl/api/v1/devices/?limit=1" -Headers $adminHeaders -TimeoutSec 20
    if ($after.count -ne $adminPage.count) { throw 'Rejected registration changed device count.' }
    $result.checks += 'ordinary_user_registration_denied_without_insert'
    Write-Output 'PASS ordinary user registration denied HTTP 403; no new device'
    $result.status='passed'
} catch {
    $result.error = 'Permission check failed; inspect the local exception and retry after fixing.'
    throw
} finally {
    if ($null -ne $createdUser) {
        try {
            if ($null -eq $userHeaders) { throw 'No test-user token available for safe cleanup.' }
            $identity = Invoke-RestMethod -Uri "$baseUrl/api/v1/users/me" -Headers $userHeaders -TimeoutSec 20
            if ($identity.id -ne $createdUser.id -or $identity.email -ne $testEmail -or $identity.is_superuser) { throw 'Test-user cleanup identity mismatch.' }
            Invoke-RestMethod -Method Delete -Uri "$baseUrl/api/v1/users/me" -Headers $userHeaders -TimeoutSec 20 | Out-Null
            $result.test_user_cleanup='deleted_own_disposable_account'
            Write-Output 'PASS disposable local test account removed; existing accounts untouched'
        } catch {
            $result.test_user_cleanup='failed'
            $result.status='failed'
            Write-Warning 'Disposable test account cleanup failed; inspect the result before retrying.'
        }
    }
    $result.checked_at=[DateTimeOffset]::UtcNow.ToString('o')
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Output "Result: $resultPath"
}
if ($result.status -ne 'passed') { throw 'Permission checks or cleanup did not complete.' }
