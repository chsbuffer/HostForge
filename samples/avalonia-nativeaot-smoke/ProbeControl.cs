using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.OpenGL;
using Avalonia.Rendering.SceneGraph;
using Avalonia.Skia;
using SkiaSharp;

namespace AvaloniaNativeAotSmoke;

internal sealed class ProbeControl : Control
{
    private readonly TaskCompletionSource _drawn = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public Task Drawn => _drawn.Task;

    public override void Render(DrawingContext context)
    {
        base.Render(context);
        context.Custom(new ProbeOperation(new Rect(Bounds.Size), _drawn));
    }

    private sealed class ProbeOperation(Rect bounds, TaskCompletionSource drawn) : ICustomDrawOperation
    {
        public Rect Bounds { get; } = bounds;
        public bool HitTest(Point point) => false;
        public bool Equals(ICustomDrawOperation? other) => false;
        public void Dispose() { }

        public void Render(ImmediateDrawingContext context)
        {
            if (drawn.Task.IsCompleted)
                return;
            try
            {
                var feature = context.TryGetFeature(typeof(ISkiaSharpApiLeaseFeature)) as ISkiaSharpApiLeaseFeature
                    ?? throw new InvalidOperationException("The window did not expose a Skia draw lease.");
                using var lease = feature.Lease();
                var result = Program.Result;
                result.ActualBackend = lease.GrContext?.Backend.ToString() ?? "Software";
                if (result.ActualBackend != result.ExpectedBackend)
                    throw new InvalidOperationException($"Requested {result.Mode}/{result.ExpectedBackend}, observed {result.ActualBackend}.");

                using (var platformLease = lease.TryLeasePlatformGraphicsApi())
                {
                    result.PlatformGraphicsContext = platformLease?.Context.GetType().FullName;
                    if (platformLease?.Context is IGlContext gl)
                    {
                        result.Renderer = gl.GlInterface.GetString(0x1F01); // GL_RENDERER of this leased context
                        result.Vendor = gl.GlInterface.GetString(0x1F00); // GL_VENDOR
                        result.GlVersion = gl.GlInterface.GetString(0x1F02);
                    }
                }
                if (result.ExpectedBackend == "OpenGL" && string.IsNullOrWhiteSpace(result.Renderer))
                    throw new InvalidOperationException("The actual OpenGL context did not expose GL_RENDERER.");
                if (result.Mode == "AngleEgl" && result.Renderer?.Contains("ANGLE", StringComparison.OrdinalIgnoreCase) != true)
                    throw new InvalidOperationException($"AngleEgl requires an ANGLE renderer, observed {result.Renderer}.");
                if (result.Mode == "AngleEgl" && Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_ANGLE_ADAPTER") is { Length: > 0 } adapter
                    && result.Renderer?.Contains(adapter, StringComparison.OrdinalIgnoreCase) != true)
                    throw new InvalidOperationException($"ANGLE renderer '{result.Renderer}' does not identify the requested adapter '{adapter}'.");

                var surface = lease.SkSurface ?? throw new InvalidOperationException("The window lease has no readable surface.");
                var canvas = lease.SkCanvas;
                canvas.Save();
                try
                {
                    canvas.ResetMatrix();
                    using var paint = new SKPaint { IsAntialias = false, BlendMode = SKBlendMode.Src };
                    SKColor[] colors = [SKColors.Red, SKColors.Lime, SKColors.Blue, SKColors.White];
                    for (int i = 0; i < colors.Length; i++)
                    {
                        paint.Color = colors[i];
                        canvas.DrawRect(16 + i % 2 * 32, 16 + i / 2 * 32, 32, 32, paint);
                    }
                    canvas.Flush();
                    lease.GrContext?.Flush(true);
                    using var pixels = new SKBitmap(new SKImageInfo(64, 64, SKColorType.Rgba8888, SKAlphaType.Premul));
                    if (!surface.ReadPixels(pixels.Info, pixels.GetPixels(), pixels.RowBytes, 16, 16))
                        throw new InvalidOperationException("Reading pixels from the actual window surface failed.");
                    var observed = new SKColor[4];
                    for (int i = 0; i < observed.Length; i++)
                        observed[i] = pixels.GetPixel(16 + i % 2 * 32, 16 + i / 2 * 32);
                    result.Pixels = observed.Select(color => color.ToString()).ToArray();
                    if (!observed.SequenceEqual(colors))
                        throw new InvalidOperationException($"Window pixels differ from the red/green/blue/white pattern: {string.Join(",", result.Pixels)}");
                    result.PixelsVerified = true;
                }
                finally
                {
                    canvas.Restore();
                }
                drawn.TrySetResult();
            }
            catch (Exception exception)
            {
                drawn.TrySetException(exception);
            }
        }
    }
}
