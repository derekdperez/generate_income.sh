using Microsoft.EntityFrameworkCore;
using NightmareV2.Application.Gatekeeping;
using NightmareV2.CommandCenter;
using NightmareV2.CommandCenter.Models;
using NightmareV2.Infrastructure.Data;

namespace NightmareV2.CommandCenter.DataMaintenance;

public static class DataMaintenanceEndpoints
{
    private const string ConfigEnabled = "Nightmare:DataMaintenance:Enabled";
    private const string ConfigApiKey = "Nightmare:DataMaintenance:ApiKey";

    public const string PhraseClearAllTargets = "DELETE ALL TARGETS";
    public const string PhraseClearAllAssets = "DELETE ALL ASSETS";
    public const string PhraseClearAssetsForDomain = "DELETE ASSETS FOR DOMAIN";

    public static void Map(WebApplication app)
    {
        app.MapGet(
                "/api/maintenance/status",
                (IConfiguration config) =>
                {
                    var enabled = config.GetValue(ConfigEnabled, false);
                    var key = config[ConfigApiKey]?.Trim();
                    return Results.Ok(new MaintenanceStatusDto(enabled, !string.IsNullOrEmpty(key)));
                })
            .WithName("MaintenanceStatus")
            .AllowAnonymous();

        app.MapPost(
                "/api/maintenance/clear-all-targets",
                async (
                    MaintenancePhraseBody body,
                    HttpRequest http,
                    IConfiguration config,
                    NightmareDbContext db,
                    IAssetDeduplicator dedup,
                    CancellationToken ct) =>
                {
                    if (!IsAllowed(config, http))
                        return MaintenanceDenied(config);

                    if (body is null || !string.Equals(body.ConfirmationPhrase?.Trim(), PhraseClearAllTargets, StringComparison.Ordinal))
                        return Results.BadRequest($"confirmationPhrase must be exactly: {PhraseClearAllTargets}");

                    await dedup.ClearAllAsync(ct).ConfigureAwait(false);
                    var n = await db.Targets.ExecuteDeleteAsync(ct).ConfigureAwait(false);
                    return Results.Ok(new MaintenanceDeleteResult("clear-all-targets", n, "Cascaded assets and high-value findings per FK."));
                })
            .WithName("MaintenanceClearAllTargets")
            .DisableAntiforgery()
            .AllowAnonymous();

        app.MapPost(
                "/api/maintenance/clear-all-assets",
                async (
                    MaintenancePhraseBody body,
                    HttpRequest http,
                    IConfiguration config,
                    NightmareDbContext db,
                    IAssetDeduplicator dedup,
                    CancellationToken ct) =>
                {
                    if (!IsAllowed(config, http))
                        return MaintenanceDenied(config);

                    if (body is null || !string.Equals(body.ConfirmationPhrase?.Trim(), PhraseClearAllAssets, StringComparison.Ordinal))
                        return Results.BadRequest($"confirmationPhrase must be exactly: {PhraseClearAllAssets}");

                    await dedup.ClearAllAsync(ct).ConfigureAwait(false);
                    var n = await db.Assets.ExecuteDeleteAsync(ct).ConfigureAwait(false);
                    return Results.Ok(new MaintenanceDeleteResult("clear-all-assets", n, "Targets and worker switches unchanged."));
                })
            .WithName("MaintenanceClearAllAssets")
            .DisableAntiforgery()
            .AllowAnonymous();

        app.MapPost(
                "/api/maintenance/clear-assets-for-domain",
                async (
                    MaintenanceClearDomainBody body,
                    HttpRequest http,
                    IConfiguration config,
                    NightmareDbContext db,
                    IAssetDeduplicator dedup,
                    CancellationToken ct) =>
                {
                    if (!IsAllowed(config, http))
                        return MaintenanceDenied(config);

                    if (body is null || !string.Equals(body.ConfirmationPhrase?.Trim(), PhraseClearAssetsForDomain, StringComparison.Ordinal))
                        return Results.BadRequest($"confirmationPhrase must be exactly: {PhraseClearAssetsForDomain}");

                    if (!TargetRootNormalization.TryNormalize(body.RootDomain ?? "", out var root))
                        return Results.BadRequest("rootDomain required");

                    var targetIds = await db.Targets.AsNoTracking()
                        .Where(t => t.RootDomain == root)
                        .Select(t => t.Id)
                        .ToListAsync(ct)
                        .ConfigureAwait(false);
                    if (targetIds.Count == 0)
                        return Results.Ok(new MaintenanceDeleteResult("clear-assets-for-domain", 0, $"No target with root domain {root}."));

                    foreach (var tid in targetIds)
                        await dedup.ClearForTargetAsync(tid, ct).ConfigureAwait(false);

                    var n = await db.Assets.Where(a => targetIds.Contains(a.TargetId)).ExecuteDeleteAsync(ct).ConfigureAwait(false);
                    return Results.Ok(new MaintenanceDeleteResult("clear-assets-for-domain", n, $"Root domain {root}."));
                })
            .WithName("MaintenanceClearAssetsForDomain")
            .DisableAntiforgery()
            .AllowAnonymous();
    }

    private static bool IsAllowed(IConfiguration config, HttpRequest http)
    {
        if (!config.GetValue(ConfigEnabled, false))
            return false;
        var required = config[ConfigApiKey]?.Trim();
        if (string.IsNullOrEmpty(required))
            return true;
        return string.Equals(http.Headers["X-Nightmare-Maintenance-Key"].ToString(), required, StringComparison.Ordinal);
    }

    private static IResult MaintenanceDenied(IConfiguration config)
    {
        if (!config.GetValue(ConfigEnabled, false))
            return Results.NotFound();
        return Results.Unauthorized();
    }
}
