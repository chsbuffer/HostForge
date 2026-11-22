using System.Buffers.Binary;
using System.Reflection.PortableExecutable;

namespace HostForge.TestInfra;

/// <summary>
/// The GuardFlags of a 64-bit PE image's load configuration, which <c>dumpbin /loadconfig</c> prints as "Guard Flags".
/// </summary>
public static class PeGuardFlags
{
    // winnt.h IMAGE_GUARD_* values.
    public const uint CfInstrumented = 0x00000100;
    public const uint EHContinuationTablePresent = 0x00400000;

    // Offset of GuardFlags in IMAGE_LOAD_CONFIG_DIRECTORY64.
    private const int GuardFlagsOffset = 0x90;

    public static uint Read(string path)
    {
        using FileStream stream = File.OpenRead(path);
        using var reader = new PEReader(stream);
        PEHeader header = reader.PEHeaders.PEHeader
            ?? throw new InvalidOperationException($"{path} is not a PE image.");
        if (header.Magic != PEMagic.PE32Plus)
            throw new InvalidOperationException($"{path} is not a 64-bit PE image.");

        DirectoryEntry loadConfig = header.LoadConfigTableDirectory;
        if (loadConfig.Size < GuardFlagsOffset + sizeof(uint))
            throw new InvalidOperationException($"{path} has no load configuration with guard flags.");

        PEMemoryBlock block = reader.GetSectionData(loadConfig.RelativeVirtualAddress);
        return BinaryPrimitives.ReadUInt32LittleEndian(block.GetContent(GuardFlagsOffset, sizeof(uint)).AsSpan());
    }

    public static void AssertSet(string path, uint expected, string description)
    {
        uint flags = Read(path);
        if ((flags & expected) != expected)
        {
            throw new InvalidOperationException(
                $"{path} lacks {description}: Guard Flags 0x{flags:X8} miss 0x{expected:X8} (dumpbin /loadconfig).");
        }
    }

    public static void AssertClear(string path, uint unexpected, string description)
    {
        uint flags = Read(path);
        if ((flags & unexpected) != 0)
        {
            throw new InvalidOperationException(
                $"{path} has {description}: Guard Flags 0x{flags:X8} include 0x{unexpected:X8} (dumpbin /loadconfig).");
        }
    }
}
