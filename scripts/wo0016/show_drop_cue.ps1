param(
    [string]$Text = '现在开始投放 10 颗 M8',
    [int]$Seconds = 12
)
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$window = New-Object System.Windows.Window
$window.WindowStyle = 'None'
$window.Topmost = $true
$window.WindowStartupLocation = 'CenterScreen'
$window.Width = 900
$window.Height = 260
$window.Background = [System.Windows.Media.Brushes]::Yellow
$label = New-Object System.Windows.Controls.TextBlock
$label.Text = $Text
$label.FontSize = 46
$label.FontWeight = 'Bold'
$label.HorizontalAlignment = 'Center'
$label.VerticalAlignment = 'Center'
$window.Content = $label
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds($Seconds)
$timer.Add_Tick({ $window.Close(); $timer.Stop() })
$timer.Start()
[System.Media.SystemSounds]::Exclamation.Play()
$window.ShowDialog() | Out-Null