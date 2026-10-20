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
    $command=$Image.Config.PSObject.Properties['Cmd']
    if($null -eq $command -or $null -eq $command.Value -or @($command.Value).Count -eq 0){throw 'Running container has no image default command to verify'}
    foreach($name in @('Cmd','Entrypoint')) {
        # Docker may omit an unset Entrypoint entirely. Keep strict mode enabled,
        # normalize absence to null, and still distinguish [] from null.
        $imageProperty=$Image.Config.PSObject.Properties[$name]
        $containerProperty=$Container.Config.PSObject.Properties[$name]
        $expected=if($null -ne $imageProperty){ConvertTo-Json -InputObject $imageProperty.Value -Compress}else{'null'}
        $actual=if($null -ne $containerProperty){ConvertTo-Json -InputObject $containerProperty.Value -Compress}else{'null'}
        if($actual -cne $expected){throw 'Running container command differs from the verified image default'}
    }
}
