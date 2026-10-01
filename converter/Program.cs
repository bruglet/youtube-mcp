using System.Drawing;
using System.Runtime.InteropServices;
using System.Xml.Linq;
using YTSubConverter.Shared;
using YTSubConverter.Shared.Formats;
using YTSubConverter.Shared.Util;

if (args.Length != 2)
{
    Console.Error.WriteLine("Usage: HeadlessCaptionConverter input.srv3 output.ass");
    return 2;
}

try
{
    using var measurer = new PangoTextMeasurer();
    var source = SubtitleDocument.Load(args[0]);
    var paragraphs = XDocument.Load(args[0]).Descendants("p").ToList();
    if (paragraphs.Count == source.Lines.Count && paragraphs.All(p => p.Attribute("w") != null && (string?)p.Attribute("a") != "1"))
    {
        // Visual conversion rounds karaoke starts to frame centers. Use the same
        // boundary for the preceding cue's end to avoid a transition overlap.
        var previous = new Dictionary<string, Line>();
        for (int index = 0; index < paragraphs.Count; index++)
        {
            var line = source.Lines[index];
            string window = paragraphs[index].Attribute("w")!.Value;
            bool emulatedKaraoke = line.Sections.Any(s => s.StartOffset > TimeSpan.Zero) &&
                line.Sections.Any(s => s.BackColor.A > 0 || s.ShadowColors.Count > 0);
            var start = emulatedKaraoke ? TimeUtil.RoundTimeToFrameCenter(line.Start) : line.Start;
            if (previous.TryGetValue(window, out var prior) && prior.End > start)
                prior.End = start;
            previous[window] = line;
        }
    }
    SubtitleDocument.Convert(source, ".ass", true, measurer).Save(args[1]);
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine(error.Message);
    return 1;
}

sealed class PangoTextMeasurer : ITextMeasurer
{
    public SizeF Measure(string text, string font, float size, bool bold, bool italic)
    {
        IntPtr context = IntPtr.Zero;
        IntPtr layout = IntPtr.Zero;
        IntPtr description = IntPtr.Zero;
        try
        {
            context = PangoFontMapCreateContext(PangoCairoFontMapGetDefault());
            layout = PangoLayoutNew(context);
            description = PangoFontDescriptionNew();
            PangoFontDescriptionSetFamily(description, font);
            PangoFontDescriptionSetAbsoluteSize(description, size * 1024);
            PangoFontDescriptionSetWeight(description, bold ? 700 : 400);
            PangoFontDescriptionSetStyle(description, italic ? 2 : 0);
            PangoLayoutSetFontDescription(layout, description);
            PangoLayoutSetText(layout, text, -1);
            PangoLayoutGetPixelSize(layout, out int width, out int height);
            return new SizeF(width, height);
        }
        finally
        {
            if (description != IntPtr.Zero) PangoFontDescriptionFree(description);
            if (layout != IntPtr.Zero) GObjectUnref(layout);
            if (context != IntPtr.Zero) GObjectUnref(context);
        }
    }

    public void Dispose() { }

    [DllImport("libpangocairo-1.0.so.0", EntryPoint = "pango_cairo_font_map_get_default")]
    private static extern IntPtr PangoCairoFontMapGetDefault();

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_font_map_create_context")]
    private static extern IntPtr PangoFontMapCreateContext(IntPtr fontMap);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_layout_new")]
    private static extern IntPtr PangoLayoutNew(IntPtr context);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_font_description_new")]
    private static extern IntPtr PangoFontDescriptionNew();

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_font_description_set_family")]
    private static extern void PangoFontDescriptionSetFamily(IntPtr description, [MarshalAs(UnmanagedType.LPUTF8Str)] string family);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_font_description_set_absolute_size")]
    private static extern void PangoFontDescriptionSetAbsoluteSize(IntPtr description, double size);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_font_description_set_weight")]
    private static extern void PangoFontDescriptionSetWeight(IntPtr description, int weight);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_font_description_set_style")]
    private static extern void PangoFontDescriptionSetStyle(IntPtr description, int style);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_layout_set_font_description")]
    private static extern void PangoLayoutSetFontDescription(IntPtr layout, IntPtr description);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_layout_set_text")]
    private static extern void PangoLayoutSetText(IntPtr layout, [MarshalAs(UnmanagedType.LPUTF8Str)] string text, int length);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_layout_get_pixel_size")]
    private static extern void PangoLayoutGetPixelSize(IntPtr layout, out int width, out int height);

    [DllImport("libpango-1.0.so.0", EntryPoint = "pango_font_description_free")]
    private static extern void PangoFontDescriptionFree(IntPtr description);

    [DllImport("libgobject-2.0.so.0", EntryPoint = "g_object_unref")]
    private static extern void GObjectUnref(IntPtr instance);
}
