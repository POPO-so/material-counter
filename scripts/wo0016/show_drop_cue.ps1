param(
    [string]$Text = '现在开始投放 10 颗 M8',
    [int]$DropSeconds = 4,
    [int]$CloseDelay = 3,
    [string]$SignalPath = '',
    [int]$WaitSeconds = 90
)
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$window = New-Object System.Windows.Window
$window.Title = 'M8 投料提示'
$window.WindowStyle = 'SingleBorderWindow'
$window.Topmost = $true
$window.WindowStartupLocation = 'CenterScreen'
$window.Width = 900
$window.Height = 300
$window.Background = [System.Windows.Media.Brushes]::Yellow
$label = New-Object System.Windows.Controls.TextBlock
$label.Text = "$Text`n$DropSeconds 秒内完成"
$label.FontSize = 44
$label.FontWeight = 'Bold'
$label.TextAlignment = 'Center'
$label.HorizontalAlignment = 'Center'
$label.VerticalAlignment = 'Center'
$window.Content = $label

$endTimer = New-Object System.Windows.Threading.DispatcherTimer
$endTimer.Interval = [TimeSpan]::FromSeconds($DropSeconds)
$endTimer.Add_Tick({
    $label.Text = '投料时间结束'
    $window.Background = [System.Windows.Media.Brushes]::LightGray
    [System.Media.SystemSounds]::Asterisk.Play()
    $endTimer.Stop()
})
$closeTimer = New-Object System.Windows.Threading.DispatcherTimer
$closeTimer.Interval = [TimeSpan]::FromSeconds($DropSeconds + $CloseDelay)
$closeTimer.Add_Tick({ $window.Close(); $closeTimer.Stop() })
$window.Add_Closed({ [System.Windows.Threading.Dispatcher]::CurrentDispatcher.InvokeShutdown() })

function Show-Now {
    $endTimer.Start()
    $closeTimer.Start()
    [System.Media.SystemSounds]::Exclamation.Play()
    $window.Show()
}

if ($SignalPath) {
    $poller = New-Object System.Windows.Threading.DispatcherTimer
    $poller.Interval = [TimeSpan]::FromMilliseconds(50)
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $poller.Add_Tick({
        if (Test-Path $SignalPath) {
            $poller.Stop()
            Show-Now
        } elseif ((Get-Date) -gt $deadline) {
            $poller.Stop()
            [System.Windows.Threading.Dispatcher]::CurrentDispatcher.InvokeShutdown()
        }
    })
    $poller.Start()
    [System.Windows.Threading.Dispatcher]::Run()
} else {
    Show-Now
    [System.Windows.Threading.Dispatcher]::Run()
}