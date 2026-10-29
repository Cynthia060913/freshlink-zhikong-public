# Opt-in, local-only application demo. No original database or real notifications.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Kit,
    [Parameter(Mandatory=$true)][string]$ExpectedManifestSha256,
    [ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedSource='e7fffd61c199234918dcf1713cc1f3dd6ce78c51',
    [switch]$Start,
    [ValidateRange(1,10)][int]$Minutes=5
)
$ErrorActionPreference='Stop'
$kitRoot=(Resolve-Path -LiteralPath $Kit).Path
$guards=Join-Path $kitRoot 'offline-kit-guards.ps1'
if((Get-FileHash -LiteralPath $guards).Hash.ToLowerInvariant() -cne '30c2183ddaeb759f2434294a2bfe60196c4b5dc5ec8c697ef9e2736f7e46ec42'){
    throw 'Unexpected validation helper; do not execute it'
}
. $guards
$manifest=Assert-OfflineKit $kitRoot $ExpectedManifestSha256
if($manifest.source -cne $ExpectedSource){throw 'Application source differs from the explicitly reviewed source'}
$runtime=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../offline-demo-runtime.py')).Path
if(-not $Start){Write-Output 'PASS package integrity. Add -Start for an isolated interactive application; no Docker action taken.';return}
$docker=Get-OfflineLocalDocker
$project='freshlink-demo-'+[guid]::NewGuid().ToString('N')
$run=Join-Path (Split-Path $kitRoot -Parent) $project
if(Test-Path -LiteralPath $run){throw 'New private runtime directory required'}
[IO.Directory]::CreateDirectory($run)|Out-Null
function New-DemoSecret {
    $bytes=New-Object byte[] 24
    $random=[Security.Cryptography.RandomNumberGenerator]::Create()
    try{$random.GetBytes($bytes)}finally{$random.Dispose()}
    return [BitConverter]::ToString($bytes).Replace('-','').ToLowerInvariant()
}
$dbPassword=New-DemoSecret
$adminPassword=New-DemoSecret
$secret=New-DemoSecret
$volume=$project+'-db'
$app=($manifest.images|Where-Object role -eq 'application').id
$pg=($manifest.images|Where-Object role -eq 'postgres').id
$mqtt=($manifest.images|Where-Object role -eq 'mqtt').id
$statePath=Join-Path $run 'demo-state.json'
$state=[ordered]@{format='freshlink-interactive-demo-v1';project=$project;source=$manifest.source;kit_manifest_sha256=$ExpectedManifestSha256;application_image=$app;database_volume=$volume;status='preparing';url=$null;runtime_seconds=$Minutes*60;real_notifications_enabled=$false;automatic_recovery_enabled=$false;created_at_utc=[DateTime]::UtcNow.ToString('o')}
function Save-DemoState {
    [IO.File]::WriteAllText($statePath,($state|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
}
function Invoke-DemoDocker([string[]]$Arguments,[string]$Log) {
    $oldPreference=$ErrorActionPreference
    try{
        $ErrorActionPreference='Continue'
        $output=@(& $docker @Arguments 2>&1|ForEach-Object {$_.ToString().Replace($dbPassword,'<redacted>').Replace($adminPassword,'<redacted>').Replace($secret,'<redacted>')})
        $code=$LASTEXITCODE
    }finally{$ErrorActionPreference=$oldPreference}
    [IO.File]::WriteAllLines((Join-Path $run $Log),$output,[Text.UTF8Encoding]::new($false))
    if($code){throw ('Owned demo operation failed; see redacted '+$Log)}
}
Save-DemoState
$environment=@{
    PROJECT_NAME='FreshLink-test';FASTAPI_ENV='development';SECRET_KEY=$secret
    FIRST_SUPERUSER='admin@example.com';FIRST_SUPERUSER_PASSWORD=$adminPassword
    DATABASE_URL=('postgresql://freshlink_test:'+$dbPassword+'@test-db:5432/freshlink_test')
    SMTP_HOST='';SMTP_PASSWORD='';EMAILS_FROM_EMAIL='';SENTRY_DSN=''
    PYTHONDONTWRITEBYTECODE='1';PYTHONPATH='/app/backend'
}
$seedEnvironment=@{};foreach($key in $environment.Keys){$seedEnvironment[$key]=$environment[$key]}
$seedEnvironment.FRESHLINK_GATED_FLEET_PROOF='1'
$seedEnvironment.GATED_FLEET_SOURCE=$manifest.source
$seedEnvironment.GATED_FLEET_DIRTY='0'
$runtimeEnvironment=@{};foreach($key in $environment.Keys){$runtimeEnvironment[$key]=$environment[$key]}
$runtimeEnvironment.FRESHLINK_INTERACTIVE_DEMO='1'
$runtimeEnvironment.FRESHLINK_DEMO_SOURCE=$manifest.source
$runtimeEnvironment.FRESHLINK_REVIEWED_DEMO_SOURCE=$ExpectedSource
$runtimeEnvironment.FRESHLINK_DEMO_SECONDS=[string]($Minutes*60)
$owner=@{'freshlink.delivery.owner'=$project}
$composeModel=@{
    services=@{
        'test-db'=@{image=$pg;labels=$owner;environment=@{POSTGRES_USER='freshlink_test';POSTGRES_PASSWORD=$dbPassword;POSTGRES_DB='freshlink_test'};volumes=@('demo-db:/var/lib/postgresql');healthcheck=@{test=@('CMD-SHELL','pg_isready -U freshlink_test -d freshlink_test');interval='2s';timeout='5s';retries=30}}
        broker=@{image=$mqtt;labels=$owner;entrypoint=@('sh','/fixtures/start.sh');volumes=@(@{type='bind';source=$kitRoot;target='/fixtures';read_only=$true});tmpfs=@('/tmp')}
        seed=@{image=$app;labels=$owner;working_dir='/app/backend';environment=$seedEnvironment;volumes=@(@{type='bind';source=$run;target='/results'});entrypoint=@('/app/.venv/bin/python','-B','-c');command=@("import subprocess,sys;subprocess.run([sys.executable,'scripts/check-gated-fleet.py','--preflight'],check=True);subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True);subprocess.run([sys.executable,'-m','alembic','check'],check=True);subprocess.run([sys.executable,'app/initial_data.py'],check=True);subprocess.run([sys.executable,'scripts/check-gated-fleet.py'],check=True)")}
        application=@{image=$app;labels=$owner;environment=$environment;ports=@(@{target=8000;published='0';host_ip='127.0.0.1'});networks=@('default','browser-edge');healthcheck=@{test=@('CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/utils/health-check/',timeout=3)");interval='2s';timeout='5s';retries=30}}
        runtime=@{image=$app;labels=$owner;network_mode='service:application';working_dir='/app/backend';environment=$runtimeEnvironment;volumes=@(@{type='bind';source=$run;target='/results'},@{type='bind';source=$runtime;target='/delivery/offline-demo-runtime.py';read_only=$true});entrypoint=@('/app/.venv/bin/python','-B','/delivery/offline-demo-runtime.py')}
    }
    networks=@{default=@{internal=$true};'browser-edge'=@{driver='bridge'}}
    volumes=@{'demo-db'=@{name=$volume;labels=$owner}}
}
$composeFile=Join-Path $run 'owned-compose.json'
# This generated file and admin-local.json are PRIVATE, outside the input kit.
[IO.File]::WriteAllText($composeFile,($composeModel|ConvertTo-Json -Depth 18),[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $run 'admin-local.json'),(@{email='admin@example.com';password=$adminPassword}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
$composeArgs=@('compose','--env-file','NUL','--project-name',$project,'-f',$composeFile)
try{
    if(@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$project)).Count -or @(& $docker volume ls -q --filter ('name=^'+$volume+'$')).Count){throw 'Refusing existing Docker resources'}
    Invoke-DemoDocker @('image','load','--quiet','--input',(Join-Path $kitRoot 'images.tar')) 'image-load.log'
    foreach($item in $manifest.images){
        $id=(& $docker image inspect $item.id --format '{{.Id}}'|Out-String).Trim()
        if($LASTEXITCODE -or $id -cne $item.id){throw 'Bundled image identity unavailable'}
    }
    $label=(& $docker image inspect $app --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'|Out-String).Trim()
    if($LASTEXITCODE -or $label -cne $manifest.source){throw 'Application source label mismatch'}
    Invoke-DemoDocker ($composeArgs+@('config','--quiet')) 'config-validation.log'
    Invoke-DemoDocker ($composeArgs+@('up','-d','--wait','--pull','never','test-db','broker')) 'dependencies.log'
    Invoke-DemoDocker ($composeArgs+@('run','--rm','--no-deps','--pull','never','seed')) 'seed.log'
    Invoke-DemoDocker ($composeArgs+@('up','-d','--no-deps','--wait','--pull','never','application')) 'application.log'
    $address=(& $docker @composeArgs port application 8000|Out-String).Trim()
    if($LASTEXITCODE -or $address -notmatch '^127\.0\.0\.1:[1-9]\d*$'){throw 'Only an ephemeral loopback URL is allowed'}
    $state.url='http://'+$address
    Invoke-DemoDocker ($composeArgs+@('up','-d','--no-deps','--pull','never','runtime')) 'runtime-start.log'
    $until=(Get-Date).AddSeconds(45)
    while(-not (Test-Path -LiteralPath (Join-Path $run 'interactive-ready.json'))){
        if(Test-Path -LiteralPath (Join-Path $run 'interactive-runtime.json')){throw 'Bounded runtime exited before readiness'}
        if((Get-Date)-gt $until){throw 'Live acquisition readiness timeout'}
        Start-Sleep -Milliseconds 250
    }
    $ready=Get-Content -LiteralPath (Join-Path $run 'interactive-ready.json') -Raw -Encoding UTF8|ConvertFrom-Json
    if($ready.source -cne $manifest.source -or $ready.stored_now -lt $ready.stored_before+14 -or $ready.real_notifications_enabled){throw 'Wrong live demo evidence'}
    $state.status='running_bounded_local_demo'
    Save-DemoState
    Write-Output (@{url=$state.url;state=$statePath;account='admin@example.com';password_file=(Join-Path $run 'admin-local.json');minutes=$Minutes;real_notifications=$false;automatic_recovery=$false}|ConvertTo-Json)
}catch{
    $state.status='failed_resources_preserved_for_scoped_stop'
    Save-DemoState
    Write-Output ('STOP: '+$_.Exception.Message)
    try{
        & (Join-Path $PSScriptRoot 'stop-offline-demo.ps1') -State $statePath
    }catch{
        Write-Output ('Scoped cleanup incomplete; inspect '+$statePath+' before any further operation.')
    }
    throw 'Interactive demo startup failed; original application data was not used'
}
