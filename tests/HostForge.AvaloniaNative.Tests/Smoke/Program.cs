using System.Globalization;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Media;
using Avalonia.Rendering.SceneGraph;
using Avalonia.Skia;
using Avalonia.Threading;
using SkiaSharp;

internal static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        // A bounded process also makes an initialization deadlock observable.
        using var watchdog = new Timer(_ => Environment.Exit(124), null, TimeSpan.FromSeconds(40), Timeout.InfiniteTimeSpan);
        return AppBuilder.Configure<SmokeApp>().UsePlatformDetect()
            .With(new AvaloniaNativePlatformOptions { RenderingMode = [AvaloniaNativeRenderingMode.Software] })
            .StartWithClassicDesktopLifetime(args);
    }
}

internal sealed class SmokeApp : Application
{
    public override void OnFrameworkInitializationCompleted()
    {
        Console.WriteLine("AVN_PLATFORM_INITIALIZED");
        if (ApplicationLifetime is not IClassicDesktopStyleApplicationLifetime lifetime)
            throw new InvalidOperationException("Desktop lifetime was not initialized.");

        var window = new Window { Width = 640, Height = 240, Title = "HostForge AvaloniaNative smoke" };
        window.Content = new SmokeControl(lifetime);
        window.Opened += (_, _) => Console.WriteLine("AVN_WINDOW_OPENED");
        lifetime.MainWindow = window;
        base.OnFrameworkInitializationCompleted();
    }
}

internal sealed class SmokeControl(IClassicDesktopStyleApplicationLifetime lifetime) : Control
{
    private readonly FrameProbe _probe = new(lifetime);

    public override void Render(DrawingContext context)
    {
        context.FillRectangle(Brushes.White, new Rect(Bounds.Size));
        string[] samples = ["System default font", "中文字体测试", "😀 🎉"];
        for (int row = 0; row < samples.Length; row++)
        {
            var text = new FormattedText(samples[row], CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface(FontFamily.Default), 28, Brushes.Black);
            context.DrawText(text, new Point(20, 20 + row * 60));
        }
        context.Custom(_probe);
    }
}

internal sealed class FrameProbe(IClassicDesktopStyleApplicationLifetime lifetime) : ICustomDrawOperation
{
    private int _completed;
    public Rect Bounds => new(0, 0, 640, 240);
    public bool HitTest(Point point) => false;
    public bool Equals(ICustomDrawOperation? other) => ReferenceEquals(this, other);
    public void Dispose() { }

    public void Render(ImmediateDrawingContext context)
    {
        if (Volatile.Read(ref _completed) != 0)
            return;
        var feature = context.TryGetFeature<ISkiaSharpApiLeaseFeature>()
            ?? throw new InvalidOperationException("No Skia drawing lease.");
        using (ISkiaSharpApiLease lease = feature.Lease())
        {
            lease.SkCanvas.Flush();
            using SKImage image = lease.SkSurface?.Snapshot()
                ?? throw new InvalidOperationException("No rendered surface.");
            using SKBitmap bitmap = SKBitmap.FromImage(image);
            double scale = bitmap.Width / Bounds.Width;
            int[] ink = new int[3];
            for (int row = 0; row < ink.Length; row++)
            {
                int top = (int)((20 + row * 60) * scale);
                int bottom = Math.Min(bitmap.Height, (int)((70 + row * 60) * scale));
                for (int y = top; y < bottom; y++)
                    for (int x = 0; x < bitmap.Width; x++)
                    {
                        SKColor color = bitmap.GetPixel(x, y);
                        if (color.Alpha > 0 && (color.Red < 230 || color.Green < 230 || color.Blue < 230))
                            ink[row]++;
                    }
                if (ink[row] < 100)
                    throw new InvalidOperationException($"Text row {row} did not render: {ink[row]} visible pixels.");
            }
            Console.WriteLine($"AVN_FRAME_RENDERED textPixels={string.Join(',', ink)}");
            string? framePath = Environment.GetEnvironmentVariable("HOSTFORGE_AVALONIANATIVE_FRAME");
            if (!string.IsNullOrEmpty(framePath))
            {
                using SKData png = image.Encode(SKEncodedImageFormat.Png, 100);
                using FileStream output = File.Create(framePath);
                png.SaveTo(output);
            }
        }
        Interlocked.Exchange(ref _completed, 1);
        Dispatcher.UIThread.Post(() =>
        {
            // Check fallback only after the first text frame, so the probe does not prewarm fonts.
            foreach (int character in new[] { (int)'A', 0x4E2D, 0x1F600 })
            {
                if (!FontManager.Current.TryMatchCharacter(character, FontStyle.Normal, FontWeight.Normal, FontStretch.Normal,
                        FontFamily.Default, CultureInfo.InvariantCulture, out Typeface typeface)
                    || !typeface.GlyphTypeface.CharacterToGlyphMap.TryGetGlyph(character, out ushort glyph) || glyph == 0)
                    throw new InvalidOperationException($"No system font glyph for U+{character:X}.");
                Console.WriteLine($"FONT U+{character:X} {typeface.GlyphTypeface.FamilyName} glyph={glyph}");
            }
            DispatcherTimer.RunOnce(() => lifetime.Shutdown(0), TimeSpan.FromMilliseconds(350));
        });
    }
}
