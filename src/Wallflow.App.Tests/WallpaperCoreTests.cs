using Wallflow.App.Core;
using Wallflow.App.Core.Sources;
using Wallflow.App.Core.Scenes;
using LibVLCSharp.Shared;
using SkiaSharp;
using System.Runtime.InteropServices;

namespace Wallflow.App.Tests;

[TestClass]
[DoNotParallelize]
public sealed class WallpaperCoreTests
{
    private string? _previousDataDirectory;
    private string _testDirectory = "";

    [TestInitialize]
    public void Initialize()
    {
        _previousDataDirectory = Environment.GetEnvironmentVariable("WALLFLOW_DATA_DIR");
        _testDirectory = Path.Combine(Path.GetTempPath(), $"wallflow-tests-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_testDirectory);
        Environment.SetEnvironmentVariable("WALLFLOW_DATA_DIR", _testDirectory);
    }

    [TestCleanup]
    public void Cleanup()
    {
        Environment.SetEnvironmentVariable("WALLFLOW_DATA_DIR", _previousDataDirectory);
        if (Directory.Exists(_testDirectory))
            Directory.Delete(_testDirectory, recursive: true);
    }

    [TestMethod]
    public void WallpaperFromPathClassifiesKindsAndUsesStableIds()
    {
        var image = Wallpaper.FromPath(Path.Combine(_testDirectory, "sunset.JPG"));
        var video = Wallpaper.FromPath(Path.Combine(_testDirectory, "loop.webm"));
        var scene = Wallpaper.FromPath(Path.Combine(_testDirectory, "particles.scene"));

        Assert.AreEqual("image", image.Kind);
        Assert.AreEqual("video", video.Kind);
        Assert.AreEqual("scene", scene.Kind);
        Assert.AreEqual(image.Id, Wallpaper.IdForPath(image.Path));
        Assert.AreEqual(image.Id, Wallpaper.IdForPath(image.Path.ToUpperInvariant()));
    }

    [TestMethod]
    public void GnomeWaylandSetsLightDarkWallpaperAndFitUsingGSettings()
    {
        var wallpaperPath = Path.Combine(_testDirectory, "wallpaper image.jpg");
        File.WriteAllBytes(wallpaperPath, [0x01]);
        var calls = new List<(string Executable, string[] Arguments)>();
        var backend = new LinuxWallpaperBackend(
            (executable, arguments) =>
            {
                calls.Add((executable, arguments.ToArray()));
                return true;
            },
            desktop: "GNOME",
            isWayland: true);

        Assert.IsTrue(backend.SetWallpaper(wallpaperPath, "fit"));
        Assert.HasCount(3, calls);
        Assert.IsTrue(calls.All(call => call.Executable == "gsettings"));
        Assert.IsTrue(calls.Any(call =>
            call.Arguments.SequenceEqual([
                "set", "org.gnome.desktop.background", "picture-uri",
                new Uri(Path.GetFullPath(wallpaperPath)).AbsoluteUri])));
        Assert.IsTrue(calls.Any(call =>
            call.Arguments.SequenceEqual([
                "set", "org.gnome.desktop.background", "picture-uri-dark",
                new Uri(Path.GetFullPath(wallpaperPath)).AbsoluteUri])));
        Assert.IsTrue(calls.Any(call =>
            call.Arguments.SequenceEqual([
                "set", "org.gnome.desktop.background", "picture-options", "scaled"])));
    }

    [TestMethod]
    public void CinnamonWaylandUsesItsOwnBackgroundSchema()
    {
        var wallpaperPath = Path.Combine(_testDirectory, "cinnamon.jpg");
        File.WriteAllBytes(wallpaperPath, [0x02]);
        var calls = new List<(string Executable, string[] Arguments)>();
        var backend = new LinuxWallpaperBackend(
            (executable, arguments) =>
            {
                calls.Add((executable, arguments.ToArray()));
                return true;
            },
            desktop: "X-Cinnamon",
            isWayland: true);

        Assert.IsTrue(backend.SetWallpaper(wallpaperPath, "stretch"));
        Assert.HasCount(2, calls);
        Assert.IsTrue(calls.All(call =>
            call.Executable == "gsettings" &&
            call.Arguments[1] == "org.cinnamon.desktop.background"));
        Assert.IsTrue(calls.Any(call => call.Arguments[^1] == "stretched"));
    }

    [TestMethod]
    public void GnomeWaylandFallsBackToDconfWithoutTryingX11Tools()
    {
        var wallpaperPath = Path.Combine(_testDirectory, "fallback.jpg");
        File.WriteAllBytes(wallpaperPath, [0x03]);
        var calls = new List<(string Executable, string[] Arguments)>();
        var backend = new LinuxWallpaperBackend(
            (executable, arguments) =>
            {
                calls.Add((executable, arguments.ToArray()));
                return executable == "dconf";
            },
            desktop: "GNOME",
            isWayland: true);

        Assert.IsTrue(backend.SetWallpaper(wallpaperPath));
        Assert.AreEqual(3, calls.Count(call => call.Executable == "dconf"));
        Assert.IsFalse(calls.Any(call => call.Executable == "feh"));
    }

    [TestMethod]
    public void GnomeFitModesMapToSupportedPictureOptions()
    {
        Assert.AreEqual("zoom", LinuxWallpaperBackend.GnomePictureOptionForFit("fill"));
        Assert.AreEqual("scaled", LinuxWallpaperBackend.GnomePictureOptionForFit("fit"));
        Assert.AreEqual("stretched", LinuxWallpaperBackend.GnomePictureOptionForFit("stretch"));
        Assert.AreEqual("centered", LinuxWallpaperBackend.GnomePictureOptionForFit("center"));
        Assert.AreEqual("wallpaper", LinuxWallpaperBackend.GnomePictureOptionForFit("tile"));
    }

    [TestMethod]
    public void GifCodecDecodesAnimatedFramesAndPreservesFrameTiming()
    {
        var gif = Convert.FromHexString(
            "47494638396101000100800000000000FFFFFF" +
            "21FF0B4E45545343415045322E300301000000" +
            "21F904000A0000002C0000000001000100000202440100" +
            "21F904000A0000002C00000000010001000002024C0100" +
            "3B");
        using var data = SKData.CreateCopy(gif);
        using var codec = SKCodec.Create(data);
        Assert.IsNotNull(codec);
        Assert.AreEqual(2, codec.FrameCount);

        var info = new SKImageInfo(1, 1, SKColorType.Rgba8888, SKAlphaType.Premul);
        var pixels = Marshal.AllocHGlobal(info.BytesSize);
        try
        {
            var first = codec.FrameInfo[0];
            var firstResult = codec.GetPixels(
                info, pixels, info.RowBytes, new SKCodecOptions(0, first.RequiredFrame));
            Assert.AreEqual(SKCodecResult.Success, firstResult);
            var firstPixel = new byte[4];
            Marshal.Copy(pixels, firstPixel, 0, firstPixel.Length);

            var second = codec.FrameInfo[1];
            var secondResult = codec.GetPixels(
                info, pixels, info.RowBytes, new SKCodecOptions(1, second.RequiredFrame));
            Assert.AreEqual(SKCodecResult.Success, secondResult);
            var secondPixel = new byte[4];
            Marshal.Copy(pixels, secondPixel, 0, secondPixel.Length);

            Assert.AreNotEqual(firstPixel[0], secondPixel[0]);
            Assert.IsTrue(codec.FrameInfo.All(frame => frame.Duration >= 0));
        }
        finally
        {
            Marshal.FreeHGlobal(pixels);
        }
    }

    [TestMethod]
    public void CommandLineParsesSupportedOptionsAndRejectsConflictingActions()
    {
        Assert.IsTrue(WallflowCommandLine.TryParse(
            ["--data-dir", "C:\\wallflow-data", "--profile=Night", "--hidden", "-v"],
            out var options,
            out var error));
        Assert.IsNull(error);
        Assert.AreEqual("C:\\wallflow-data", options.DataDirectory);
        Assert.AreEqual("Night", options.ProfileName);
        Assert.IsTrue(options.Hidden);
        Assert.IsTrue(options.Verbose);

        Assert.IsFalse(WallflowCommandLine.TryParse(
            ["--set", "wallpaper.jpg", "--list-monitors"],
            out _,
            out error));
        Assert.IsNotNull(error);
    }

    [TestMethod]
    public void WebWallpaperProjectLoadsMetadataAndSupportsStandaloneHtmlFiles()
    {
        var projectDirectory = Path.Combine(_testDirectory, "Aurora");
        Directory.CreateDirectory(projectDirectory);
        File.WriteAllText(Path.Combine(projectDirectory, "index.html"), "<html></html>");
        File.WriteAllText(Path.Combine(projectDirectory, "project.json"),
            """{"title":"Aurora","author":"Artist","description":"Night sky","background":"#04070f","preview":"cover.jpg"}""");
        File.WriteAllBytes(Path.Combine(projectDirectory, "cover.jpg"), [0x01]);

        var project = WebWallpaperProject.Load(projectDirectory);
        Assert.IsTrue(project.IsValid);
        Assert.AreEqual("Aurora", project.Title);
        Assert.AreEqual("Artist", project.Author);
        Assert.AreEqual("Night sky", project.Description);
        Assert.AreEqual("#04070f", project.Background);
        Assert.AreEqual(Path.Combine(projectDirectory, "cover.jpg"), project.PreviewPath);

        var standalone = Path.Combine(_testDirectory, "single.html");
        File.WriteAllText(standalone, "<html></html>");
        Assert.AreEqual(standalone, WebWallpaperProject.Load(standalone).EntryPath);
    }

    [TestMethod]
    public void WebWallpaperProjectReportsBadMetadataWithoutRejectingThePage()
    {
        var projectDirectory = Path.Combine(_testDirectory, "BrokenMetadata");
        Directory.CreateDirectory(projectDirectory);
        var entryPath = Path.Combine(projectDirectory, "index.html");
        File.WriteAllText(entryPath, "<html></html>");
        File.WriteAllText(Path.Combine(projectDirectory, "project.json"), "{ invalid");

        var project = WebWallpaperProject.Load(projectDirectory);

        Assert.IsTrue(project.IsValid);
        Assert.AreEqual(entryPath, project.EntryPath);
        Assert.IsNotNull(project.MetadataError);
        Assert.AreEqual("BrokenMetadata", project.Title);
    }

    [TestMethod]
    public void BundledWindowsLibVlcRuntimeInitializes()
    {
        if (!OperatingSystem.IsWindows())
            return;

        global::LibVLCSharp.Shared.Core.Initialize();
        using var libVlc = new LibVLC("--no-video-title-show");
        Assert.IsNotNull(libVlc);
    }

    [TestMethod]
    public void LibraryScansSearchesAndPersistsFavouriteState()
    {
        var pictures = Path.Combine(_testDirectory, "Pictures");
        Directory.CreateDirectory(pictures);
        File.WriteAllBytes(Path.Combine(pictures, "Northern Lights.jpg"), [0x01, 0x02]);
        File.WriteAllBytes(Path.Combine(pictures, "loop.mp4"), [0x03]);
        Directory.CreateDirectory(Path.Combine(pictures, ".cache"));
        File.WriteAllBytes(Path.Combine(pictures, ".cache", "hidden.jpg"), [0x04]);

        var config = new WallflowConfig();
        config.AddLibraryDirectories([pictures]);
        var library = new WallpaperLibrary(config);

        Assert.AreEqual(2, library.Scan());
        var match = library.Search("lights").Single();
        library.SetFavourite(match.Id, true);
        library.Update(match.Id, name: "Aurora", author: "Artist", description: "Night sky", tags: ["night"]);

        var reloaded = new WallpaperLibrary(new WallflowConfig());
        var updated = reloaded.Search("").Single(item => item.Id == match.Id);
        Assert.IsTrue(updated.Favourite);
        Assert.AreEqual("Aurora", updated.Name);
        Assert.AreEqual("Artist", updated.Author);
        Assert.AreEqual("Night sky", updated.Description);
        CollectionAssert.AreEqual(new[] { "night" }, updated.Tags);
        Assert.AreEqual("video", reloaded.Search("loop").Single().Kind);
    }

    [TestMethod]
    public void LibraryPicksUpNewDirectoriesAndCopiesImportedFiles()
    {
        var config = new WallflowConfig();
        var library = new WallpaperLibrary(config);
        var outside = Path.Combine(_testDirectory, "Outside");
        Directory.CreateDirectory(outside);
        var externalImage = Path.Combine(outside, "forest.png");
        File.WriteAllBytes(externalImage, [0x05]);

        config.AddLibraryDirectories([outside]);
        Assert.AreEqual(1, library.Scan());
        Assert.AreEqual("forest", library.Search("").Single().Name);

        var added = library.AddFiles([externalImage]);
        Assert.AreEqual(1, added);
        Assert.IsTrue(library.Search("").Any(item =>
            item.Path.StartsWith(WallflowPaths.WallpaperDirectory, StringComparison.OrdinalIgnoreCase)));
    }

    [TestMethod]
    public void EmptyWallpaperFilesAreSkippedAndDroppedFromTheLibrary()
    {
        var pictures = Path.Combine(_testDirectory, "Pictures");
        Directory.CreateDirectory(pictures);
        var broken = Path.Combine(pictures, "broken.mp4");
        var good = Path.Combine(pictures, "good.jpg");
        File.WriteAllBytes(broken, [0x08]);
        File.WriteAllBytes(good, [0x09]);

        var config = new WallflowConfig();
        config.AddLibraryDirectories([pictures]);
        var library = new WallpaperLibrary(config);
        Assert.AreEqual(2, library.Scan());
        Assert.IsNotNull(library.Get(Wallpaper.IdForPath(broken)));

        Assert.IsTrue(WallpaperLibrary.IsUsable(good));
        Assert.IsTrue(WallpaperLibrary.IsUsable(pictures));
        Assert.IsFalse(WallpaperLibrary.IsUsable(Path.Combine(pictures, "missing.jpg")));

        // A failed download leaves a zero-byte file: it stops being a wallpaper.
        File.WriteAllBytes(broken, []);
        Assert.IsFalse(WallpaperLibrary.IsUsable(broken));
        Assert.AreEqual(1, library.Scan(), "only the intact file may be indexed");
        Assert.IsNull(library.Get(Wallpaper.IdForPath(broken)));
        Assert.HasCount(1, library.Search(""));

        // Importing ignores it as well.
        Assert.AreEqual(1, library.AddFiles([broken, good]));
        Assert.AreEqual(1, library.ImportDirectory(pictures));
    }

    [TestMethod]
    public void LibraryFiltersByKindFavouriteTagAndFolder()
    {
        var pictures = Path.Combine(_testDirectory, "Pictures");
        var nested = Path.Combine(pictures, "Nested");
        Directory.CreateDirectory(nested);
        File.WriteAllBytes(Path.Combine(pictures, "still.jpg"), [0x06]);
        File.WriteAllBytes(Path.Combine(nested, "motion.mp4"), [0x07]);
        var config = new WallflowConfig();
        config.AddLibraryDirectories([pictures]);
        var library = new WallpaperLibrary(config);
        library.Scan();

        var video = library.Search("motion").Single();
        library.SetFavourite(video.Id, true);
        library.Update(video.Id, tags: ["animated", "night"]);

        Assert.AreEqual(video.Id, library.Filter(kinds: [WallpaperKind.Video], favouritesOnly: true, tags: ["night"]).Single().Id);
        Assert.HasCount(1, library.Filter(folder: nested));
        Assert.HasCount(1, library.Filter(kinds: [WallpaperKind.Image]));
    }

    [TestMethod]
    public void ScheduleRuleSupportsWeekdaysAndOvernightIntervals()
    {
        var rule = new ScheduleRule
        {
            Enabled = true,
            Weekdays = [0, 1],
            StartMinutes = 22 * 60,
            EndMinutes = 6 * 60
        };

        Assert.IsTrue(rule.Matches(23 * 60, weekday: 0));
        Assert.IsTrue(rule.Matches(2 * 60, weekday: 1));
        Assert.IsFalse(rule.Matches(12 * 60, weekday: 0));
        Assert.IsFalse(rule.Matches(23 * 60, weekday: 2));
    }

    [TestMethod]
    public void ProfilePlaylistAndRulesRoundTripInPythonCompatibleJson()
    {
        var config = new WallflowConfig();
        var profile = new Profile { Name = "Evening", WallpaperId = "wallpaper-1" };
        var playlist = new Playlist { Name = "Rotation", Items = ["wallpaper-1"], Shuffle = true };
        var rule = new ScheduleRule { Name = "Night", ProfileId = profile.Id, StartMinutes = 20 * 60 };

        config.SaveProfiles([profile]);
        config.SavePlaylists([playlist]);
        config.SaveRules([rule]);

        Assert.AreEqual(profile.Id, new WallflowConfig().LoadProfiles().Single().Id);
        Assert.AreEqual("wallpaper-1", new WallflowConfig().LoadPlaylists().Single().Items.Single());
        Assert.AreEqual(profile.Id, new WallflowConfig().LoadRules().Single().ProfileId);
        StringAssert.Contains(File.ReadAllText(Path.Combine(_testDirectory, "profiles.json")), "\"wallpaper_id\"");
    }

    [TestMethod]
    public void PlaybackControllerActivatesProfilesAndAdvancesPlaylists()
    {
        var pictures = Path.Combine(_testDirectory, "Pictures");
        Directory.CreateDirectory(pictures);
        var firstPath = Path.Combine(pictures, "first.jpg");
        var secondPath = Path.Combine(pictures, "second.jpg");
        File.WriteAllBytes(firstPath, [0x10]);
        File.WriteAllBytes(secondPath, [0x11]);

        var config = new WallflowConfig();
        config.AddLibraryDirectories([pictures]);
        var library = new WallpaperLibrary(config);
        library.Scan();
        var player = new FakePlayback();
        using var controller = new PlaybackController(config, library, player);
        var first = library.Search("first").Single();
        var second = library.Search("second").Single();
        var profile = controller.AddProfile("Work", first.Id);
        var playlist = controller.AddPlaylist("Rotation", [first.Id, second.Id]);
        profile.AccentColor = "#5b8cff";
        profile.SetMonitor("DISPLAY2", second.Id, "fit", "left");
        var copy = controller.DuplicateProfile(profile.Id);

        Assert.AreNotEqual(profile.Id, copy.Id);
        Assert.AreEqual("Work (копия)", copy.Name);
        Assert.AreEqual(profile.WallpaperId, copy.WallpaperId);
        Assert.AreEqual(profile.AccentColor, copy.AccentColor);
        Assert.AreEqual(second.Id, copy.Monitors.Single().WallpaperId);
        Assert.AreEqual("fit", copy.Monitors.Single().Fit);
        Assert.AreNotSame(profile.Monitors.Single(), copy.Monitors.Single());
        controller.RenameProfile(profile.Id, "Work profile");
        controller.SetProfilePlaylist(profile.Id, playlist.Id);
        Assert.AreEqual("Work profile", profile.Name);
        Assert.AreEqual(playlist.Id, profile.PlaylistId);
        Assert.IsNull(profile.WallpaperId);
        Assert.IsTrue(controller.ActivateProfile(profile.Id, remember: false));
        Assert.AreEqual(first.Id, player.Current?.Id);
        controller.SetPlaylist(playlist.Id);
        Assert.AreEqual(first.Id, player.Current?.Id);
        controller.Next();
        Assert.AreEqual(second.Id, player.Current?.Id);
        controller.SetMonitorWallpaper("DISPLAY2", second.Id, "fit", "left-top");
        Assert.AreEqual(second.Id, player.MonitorAssignments?["DISPLAY2"]?.Id);
        Assert.AreEqual("fit", player.MonitorAssignments?["DISPLAY2"]?.DisplayFit);
        Assert.AreEqual("left-top", player.MonitorAssignments?["DISPLAY2"]?.DisplayAlignment);
        Assert.AreEqual("fill", second.DisplayFit);

        var savedProfile = File.ReadAllText(Path.Combine(WallflowPaths.DataDirectory, "profiles.json"));
        StringAssert.Contains(savedProfile, "\"fit\": \"fit\"");
        StringAssert.Contains(savedProfile, "\"alignment\": \"left-top\"");
        var savedLibrary = File.ReadAllText(WallflowPaths.LibraryFile);
        Assert.DoesNotContain("\"display_fit\"", savedLibrary);
        Assert.DoesNotContain("\"display_alignment\"", savedLibrary);

        playlist.Transition = "none";
        playlist.TransitionMs = 1200;
        controller.SetPlaylist(playlist.Id);
        Assert.AreEqual(0, player.TransitionMs);
        playlist.Transition = "fade";
        playlist.TransitionMs = 450;
        controller.SavePlaylist(playlist);
        controller.SetPlaylist(playlist.Id);
        Assert.AreEqual(450, player.TransitionMs);
    }

    [TestMethod]
    public void SourceCatalogAndDownloadHelpersPreserveProviderMetadata()
    {
        var providers = WallpaperSources.BuildProviders();
        Assert.HasCount(8, providers);
        Assert.IsTrue(providers.Any(provider => provider.Name == "bing" && provider.Enabled));
        Assert.IsFalse(providers.Single(provider => provider.Name == "pexels").Enabled);

        var result = new SearchResult
        {
            Id = "sample",
            Url = "https://example.test/forest.webp?size=large",
            Title = "Forest at dawn",
            Author = "Artist",
            SourceUrl = "https://example.test/photo",
            Provider = "openverse",
            License = "CC BY"
        };
        Assert.AreEqual("openverse-Forest at dawn.webp", WallpaperSources.SuggestFilename(result));
        Assert.AreEqual(".jpg", WallpaperSources.GuessExtension("https://example.test/picture.jpeg"));
        Assert.AreEqual(16.0, result.Aspect);
    }

    [TestMethod]
    public void CorruptSettingsRecoverFromBackupAndCanResetToDefaults()
    {
        var config = new WallflowConfig();
        config.Set("video_audio", true);
        config.Set("battery_pause_threshold", 35);
        config.Set("idle_minutes", 10);
        config.Set("minimize_to_tray", false);
        config.Set("close_to_tray", false);
        config.Set("static_wallpaper_mode", "window");
        config.Set("max_fps", 55);
        config.Set("video_audio", false);

        var configPath = Path.Combine(_testDirectory, "config.json");
        File.WriteAllText(configPath, "{ invalid json");

        var recovered = new WallflowConfig();
        Assert.IsTrue(recovered.Get("video_audio", false));
        Assert.AreEqual(35, recovered.Get("battery_pause_threshold", 20));
        Assert.AreEqual(10, recovered.Get("idle_minutes", 5));
        Assert.IsFalse(recovered.Get("minimize_to_tray", true));
        Assert.IsFalse(recovered.Get("close_to_tray", true));
        Assert.AreEqual("window", recovered.Get("static_wallpaper_mode", "auto"));
        Assert.AreEqual(55, recovered.Get("max_fps", 30));
        Assert.IsTrue(Directory.EnumerateFiles(Path.Combine(_testDirectory, "backups"), "config-*.json").Any());

        recovered.ResetDefaults();
        Assert.IsFalse(new WallflowConfig().Get("video_audio", true));
        Assert.AreEqual(20, new WallflowConfig().Get("battery_pause_threshold", -1));
        Assert.AreEqual(5, new WallflowConfig().Get("idle_minutes", -1));
        Assert.IsTrue(new WallflowConfig().Get("minimize_to_tray", false));
        Assert.IsTrue(new WallflowConfig().Get("close_to_tray", false));
        Assert.AreEqual("auto", new WallflowConfig().Get("static_wallpaper_mode", ""));
        Assert.AreEqual(30, new WallflowConfig().Get("max_fps", -1));
    }

    [TestMethod]
    public void BuiltinSceneCanBeSavedAndLoaded()
    {
        var scene = SceneSpec.Builtin("waves", name: "Test waves");
        var scenePath = Path.Combine(_testDirectory, "waves.scene");
        scene.Save(scenePath);

        var loaded = SceneSpec.Load(scenePath);
        Assert.IsTrue(loaded.ToJson().Contains("waves", StringComparison.Ordinal));
        Assert.IsTrue(loaded.ToJson().Contains("Test waves", StringComparison.Ordinal));
    }

    [TestMethod]
    public void MatchingScheduleRuleWinsWhenPlaybackStarts()
    {
        var config = new WallflowConfig();
        var library = new WallpaperLibrary(config);
        var player = new FakePlayback();
        var profile = new Profile { Name = "Scheduled profile" };
        config.SaveProfiles([profile]);
        config.SaveRules([new ScheduleRule
        {
            Name = "All day",
            ProfileId = profile.Id,
            StartMinutes = 0,
            EndMinutes = 24 * 60
        }]);

        using var controller = new PlaybackController(config, library, player);
        controller.Start();

        Assert.AreEqual(profile.Id, controller.ActiveProfile?.Id);
    }

    [TestMethod]
    public void ScheduleRuleUpdatesPersistDaysEnabledPriorityAndTimeRange()
    {
        var config = new WallflowConfig();
        var library = new WallpaperLibrary(config);
        var profile = new Profile { Name = "Weekday profile" };
        var rule = new ScheduleRule
        {
            Name = "Initial",
            ProfileId = profile.Id,
            StartMinutes = 0,
            EndMinutes = 60
        };
        config.SaveProfiles([profile]);
        config.SaveRules([rule]);

        using var controller = new PlaybackController(config, library, new FakePlayback());
        rule = controller.Rules.Single();
        rule.Name = "Work hours";
        rule.Enabled = false;
        rule.Weekdays = [0, 1, 2, 3, 4];
        rule.StartMinutes = 9 * 60;
        rule.EndMinutes = 17 * 60 + 30;
        rule.Priority = 7;
        controller.UpdateRule(rule);

        var saved = new WallflowConfig().LoadRules().Single();
        Assert.AreEqual("Work hours", saved.Name);
        Assert.IsFalse(saved.Enabled);
        CollectionAssert.AreEqual(new[] { 0, 1, 2, 3, 4 }, saved.Weekdays);
        Assert.AreEqual(9 * 60, saved.StartMinutes);
        Assert.AreEqual(17 * 60 + 30, saved.EndMinutes);
        Assert.AreEqual(7, saved.Priority);
    }

    [TestMethod]
    public void ManualPauseAndResumeReachPlaybackEngine()
    {
        var config = new WallflowConfig();
        config.Set("pause_on_battery", false);
        config.Set("pause_on_fullscreen", false);
        config.Set("pause_on_lock", false);
        config.Set("pause_on_remote_session", false);
        var player = new FakePlayback();
        using var controller = new PlaybackController(config, new WallpaperLibrary(config), player);

        controller.SetManualPause(true);
        Assert.IsTrue(controller.Paused);
        Assert.IsTrue(player.Paused);
        controller.SetManualPause(false);
        Assert.IsFalse(controller.Paused);
        Assert.IsFalse(player.Paused);
    }

    [TestMethod]
    public void ConfigSetNotifiesListenersAndToleratesBrokenListeners()
    {
        var config = new WallflowConfig();
        var notified = new List<(string Key, object? Value)>();
        config.AddListener((key, value) =>
        {
            throw new InvalidOperationException("broken listener");
        });
        config.AddListener((key, value) => notified.Add((key, value)));

        config.Set("video_audio", true);
        config.Set("boom", 1);
        config.Set("loop_videos", false);

        Assert.AreEqual(3, notified.Count);
        Assert.AreEqual("video_audio", notified[0].Key);
        Assert.AreEqual("boom", notified[1].Key);
        Assert.AreEqual("loop_videos", notified[2].Key);
        Assert.IsTrue(config.Get("video_audio", false));
        Assert.IsFalse(config.Get("loop_videos", true));
    }

    [TestMethod]
    public void LibraryLookupByPathFindsTheWallpaper()
    {
        Directory.CreateDirectory(WallflowPaths.WallpaperDirectory);
        var path = Path.Combine(_testDirectory, "wallpaper.jpg");
        File.WriteAllBytes(path, [0x01]);

        var library = new WallpaperLibrary(new WallflowConfig());
        Assert.IsNull(library.ByPath(Path.Combine(_testDirectory, "missing.jpg")));

        var imported = Path.Combine(_testDirectory, "imported.jpg");
        File.Copy(path, imported);
        library.AddFiles([imported]);

        var copiedPath = Path.Combine(WallflowPaths.WallpaperDirectory, "imported.jpg");
        Assert.IsNotNull(library.ByPath(copiedPath));
        Assert.IsNull(library.ByPath(Path.Combine(_testDirectory, "missing.jpg")));
    }

    [TestMethod]
    public void LinuxBackendAppliesAccentColourForSway()
    {
        var calls = new List<(string Executable, string[] Arguments)>();
        var backend = new LinuxWallpaperBackend(
            (executable, arguments) =>
            {
                calls.Add((executable, arguments.ToArray()));
                return true;
            },
            desktop: "Sway",
            isWayland: true);

        Assert.IsTrue(backend.SetAccentColor("#ff8800"));
        Assert.HasCount(1, calls);
        Assert.IsTrue(calls[0].Executable == "swaymsg");
        Assert.IsTrue(calls[0].Arguments.SequenceEqual([
            "output", "*", "bg", "#ff8800", "solid_color"]));
    }

    [TestMethod]
    public void BackendHelpersNormaliseHexColours()
    {
        Assert.AreEqual("#0b1020", WallpaperBackendHelpers.NormalizeHex("#0b1020"));
        Assert.AreEqual("#abcdef", WallpaperBackendHelpers.NormalizeHex("ABCDEF"));
        Assert.AreEqual("#112233", WallpaperBackendHelpers.NormalizeHex("#11223344"));
        Assert.AreEqual("#000000", WallpaperBackendHelpers.NormalizeHex("not-a-colour"));
    }

    [TestMethod]
    public void AccentColorPicksTheDominantSaturatedHue()
    {
        var red = WritePng("accent-red.png", 160, 120, new SKColor(0xcc, 0x22, 0x22));
        var blue = WritePng("accent-blue.png", 160, 120, new SKColor(0x22, 0x33, 0xcc));

        var redAccent = AccentColor.Dominant(red);
        var blueAccent = AccentColor.Dominant(blue);

        Assert.AreNotEqual(AccentColor.Fallback, redAccent);
        Assert.AreNotEqual(AccentColor.Fallback, blueAccent);
        var redChannels = ParseHex(redAccent);
        var blueChannels = ParseHex(blueAccent);
        Assert.IsTrue(redChannels.Red > redChannels.Green && redChannels.Red > redChannels.Blue);
        Assert.IsTrue(blueChannels.Blue > blueChannels.Green && blueChannels.Blue > blueChannels.Red);
        Assert.AreEqual(AccentColor.Fallback, AccentColor.Dominant(Path.Combine(_testDirectory, "missing.png")));
    }

    [TestMethod]
    public void ContrastingTextFollowsLuma()
    {
        Assert.AreEqual("#000000", AccentColor.ContrastingText("#ffffff"));
        Assert.AreEqual("#ffffff", AccentColor.ContrastingText("#101010"));
        Assert.AreEqual("#ffffff", AccentColor.ContrastingText("not-a-colour"));
    }

    [TestMethod]
    public void MediaInfoReadsImageResolutionAndFormatsSizes()
    {
        var path = WritePng("resolution.png", 320, 200, new SKColor(0x40, 0x80, 0xc0));

        var size = MediaInfo.TryGetImageSize(path);

        Assert.AreEqual(new MediaSize(320, 200), size);
        Assert.AreEqual("320x200", MediaInfo.Describe(size!.Value));
        Assert.AreEqual("3840x2160 (4K)", MediaInfo.Describe(new MediaSize(3840, 2160)));
        Assert.IsNull(MediaInfo.TryGetImageSize(Path.Combine(_testDirectory, "none.png")));
    }

    [TestMethod]
    public void UpscaleWarningDescribesTheStretchFactor()
    {
        var warning = WallpaperSurfaceWindow.UpscaleWarning("Ролик", new MediaSize(1280, 720), 1920, 1080);

        StringAssert.Contains(warning, "1280×720");
        StringAssert.Contains(warning, "1920×1080");
        StringAssert.Contains(warning, "1.5");
        StringAssert.Contains(warning, "1080p");
    }

    [TestMethod]
    public void HumanizeFormatsSizesAndDurations()
    {
        Assert.AreEqual("0 Б", Humanize.Bytes(0));
        Assert.AreEqual("512 Б", Humanize.Bytes(512));
        Assert.AreEqual("1.5 КБ", Humanize.Bytes(1536));
        Assert.AreEqual("2 МБ", Humanize.Bytes(2 * 1024 * 1024));
        Assert.AreEqual("45 с", Humanize.Duration(TimeSpan.FromSeconds(45)));
        Assert.AreEqual("1 мин 30 с", Humanize.Duration(TimeSpan.FromSeconds(90)));
        Assert.AreEqual("1 ч 1 мин", Humanize.Duration(TimeSpan.FromSeconds(3660)));
    }

    [TestMethod]
    public void LibraryDirectoriesCanBeAddedAndRemoved()
    {
        var config = new WallflowConfig();
        var folder = Path.Combine(_testDirectory, "extra-library");
        Directory.CreateDirectory(folder);

        config.AddLibraryDirectories([folder]);
        Assert.HasCount(1, config.LibraryDirectories);

        config.RemoveLibraryDirectory(folder);
        Assert.HasCount(0, config.LibraryDirectories);
    }

    [TestMethod]
    public void LibraryRemoveCanDeleteTheFile()
    {
        var path = WritePng("delete-me.png", 64, 64, new SKColor(0x20, 0x30, 0x40));
        var library = new WallpaperLibrary(new WallflowConfig());
        Assert.AreEqual(1, library.AddFiles([path]));
        var added = library.ByPath(Path.Combine(WallflowPaths.WallpaperDirectory, "delete-me.png"));

        Assert.IsNotNull(added);
        Assert.IsTrue(File.Exists(added.Path));
        Assert.IsTrue(library.Remove(added.Id, deleteFile: true));

        Assert.IsFalse(File.Exists(added.Path));
        Assert.IsNull(library.Get(added.Id));
    }

    [TestMethod]
    public void DesktopBackendsAdvertiseTheirCapabilities()
    {
        var gnome = new LinuxWallpaperBackend(desktop: "GNOME", isWayland: true);
        var sway = new LinuxWallpaperBackend(desktop: "Sway", isWayland: true);

        Assert.IsFalse(gnome.Capabilities.PerMonitor);
        Assert.IsTrue(sway.Capabilities.PerMonitor);
        Assert.IsTrue(sway.Capabilities.AccentColor);

        var session = DesktopEnvironment.Detect();
        Assert.IsFalse(string.IsNullOrEmpty(session.Label));
        Assert.IsTrue(session.SessionType is "x11" or "wayland" or "headless");
    }

    private string WritePng(string name, int width, int height, SKColor color)
    {
        var path = Path.Combine(_testDirectory, name);
        using var bitmap = new SKBitmap(width, height);
        using (var canvas = new SKCanvas(bitmap))
            canvas.Clear(color);
        using var image = SKImage.FromBitmap(bitmap);
        using var data = image.Encode(SKEncodedImageFormat.Png, 100);
        using var stream = File.Create(path);
        data.SaveTo(stream);
        return path;
    }

    private static (int Red, int Green, int Blue) ParseHex(string color) => (
        Convert.ToInt32(color.Substring(1, 2), 16),
        Convert.ToInt32(color.Substring(3, 2), 16),
        Convert.ToInt32(color.Substring(5, 2), 16));

    private sealed class FakePlayback : IWallpaperPlayback
    {
        public IReadOnlyList<MonitorInfo> Monitors => [];
        public Wallpaper? Current { get; private set; }
        public IReadOnlyDictionary<string, Wallpaper?>? MonitorAssignments { get; private set; }
        public int TransitionMs { get; private set; }
        public bool Paused { get; private set; }
        public event EventHandler<Wallpaper?>? WallpaperChanged;
        public event EventHandler<bool>? PauseChanged;
        public event EventHandler<string>? StatusMessage;

        public void SetCurrent(
            Wallpaper? wallpaper,
            IReadOnlyDictionary<string, Wallpaper?>? monitors = null,
            int transitionMs = 800)
        {
            Current = wallpaper;
            MonitorAssignments = monitors;
            TransitionMs = transitionMs;
            WallpaperChanged?.Invoke(this, wallpaper);
            if (wallpaper is not null)
                StatusMessage?.Invoke(this, $"Applied {wallpaper.Name}");
        }

        public void SetPaused(bool paused)
        {
            Paused = paused;
            PauseChanged?.Invoke(this, paused);
        }

        public bool ApplyAccent(string color) => true;
    }
}
