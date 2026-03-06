using System.Runtime.InteropServices;

[DllImport("STUB")]
static extern int plus(int a, int b);

DllImportResolver dllImportResolver = (name, assembly, path) =>
{
    if (name is "STUB")
    {
        return NativeLibrary.GetMainProgramHandle();
    }

    return IntPtr.Zero;
};
NativeLibrary.SetDllImportResolver(typeof(Locator).Assembly, dllImportResolver);

Console.WriteLine("Hello, World!" + plus(1, 1));

class Locator;
