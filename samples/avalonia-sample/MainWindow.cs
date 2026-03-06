using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;

namespace AvaloniaSample;

internal sealed class MainWindow : Window
{
    public MainWindow()
    {
        Title = "HostForge Avalonia Sample";
        Width = 960;
        Height = 600;
        Content = new TextBlock
        {
            Text = "Avalonia app is running with ChsBuffer.Avalonia.AppHost.",
            Margin = new Thickness(24),
            TextWrapping = Avalonia.Media.TextWrapping.Wrap,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center
        };
    }
}
