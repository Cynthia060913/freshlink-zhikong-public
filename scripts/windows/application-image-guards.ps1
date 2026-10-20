function Assert-FreshLinkDefaultImageCommand($Service) {
    foreach($name in @('command','entrypoint')) {
        $property=$Service.PSObject.Properties[$name]
        # Compose normalizes an omitted field to null. Empty []/'' deliberately
        # overrides image defaults and is still forbidden, not treated as false.
        if($null -ne $property -and $null -ne $property.Value){throw 'Default application CMD must not be overridden'}
    }
}

function Assert-FreshLinkRunningImageIdentity($Image,$Container) {
    if($Container.Image -ne $Image.Id){throw 'Running container does not use the verified image ID'}
    foreach($name in @('Cmd','Entrypoint')) {
        $expected=ConvertTo-Json -InputObject $Image.Config.$name -Compress
        $actual=ConvertTo-Json -InputObject $Container.Config.$name -Compress
        if($actual -cne $expected){throw 'Running container command differs from the verified image default'}
    }
}
