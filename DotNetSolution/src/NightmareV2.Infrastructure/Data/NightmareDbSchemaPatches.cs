using Microsoft.EntityFrameworkCore;

namespace NightmareV2.Infrastructure.Data;

/// <summary>
/// EF <c>EnsureCreated</c> does not add new columns to existing databases; run these patches after it on upgrade.
/// </summary>
public static class NightmareDbSchemaPatches
{
    public static async Task ApplyAfterEnsureCreatedAsync(NightmareDbContext db, CancellationToken cancellationToken = default)
    {
        await db.Database.ExecuteSqlRawAsync(
                """
                ALTER TABLE bus_journal ADD COLUMN IF NOT EXISTS host_name character varying(256) NOT NULL DEFAULT '';
                """,
                cancellationToken)
            .ConfigureAwait(false);

        await db.Database.ExecuteSqlRawAsync(
                """
                CREATE TABLE IF NOT EXISTS high_value_findings (
                    id uuid NOT NULL PRIMARY KEY,
                    target_id uuid NOT NULL REFERENCES recon_targets(id) ON DELETE CASCADE,
                    source_asset_id uuid NULL,
                    finding_type character varying(64) NOT NULL,
                    severity character varying(32) NOT NULL,
                    pattern_name character varying(256) NOT NULL,
                    category character varying(128) NULL,
                    matched_text text NULL,
                    source_url character varying(4096) NOT NULL,
                    worker_name character varying(128) NOT NULL,
                    importance_score integer NULL,
                    discovered_at_utc timestamp with time zone NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_high_value_findings_target_id ON high_value_findings (target_id);
                CREATE INDEX IF NOT EXISTS ix_high_value_findings_discovered_at ON high_value_findings (discovered_at_utc DESC);
                """,
                cancellationToken)
            .ConfigureAwait(false);
    }
}
