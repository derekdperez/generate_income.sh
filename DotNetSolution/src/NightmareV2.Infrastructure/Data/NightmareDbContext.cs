using Microsoft.EntityFrameworkCore;
using NightmareV2.Domain.Entities;

namespace NightmareV2.Infrastructure.Data;

public sealed class NightmareDbContext(DbContextOptions<NightmareDbContext> options) : DbContext(options)
{
    public DbSet<ReconTarget> Targets => Set<ReconTarget>();
    public DbSet<StoredAsset> Assets => Set<StoredAsset>();
    public DbSet<BusJournalEntry> BusJournal => Set<BusJournalEntry>();
    public DbSet<WorkerSwitch> WorkerSwitches => Set<WorkerSwitch>();
    public DbSet<HighValueFinding> HighValueFindings => Set<HighValueFinding>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<ReconTarget>(e =>
        {
            e.ToTable("recon_targets");
            e.HasKey(x => x.Id);
            e.Property(x => x.RootDomain).HasMaxLength(253).IsRequired();
            e.HasIndex(x => x.RootDomain).IsUnique();
        });

        modelBuilder.Entity<StoredAsset>(e =>
        {
            e.ToTable("stored_assets");
            e.HasKey(x => x.Id);
            e.Property(x => x.CanonicalKey).HasMaxLength(2048).IsRequired();
            e.Property(x => x.RawValue).HasMaxLength(4096).IsRequired();
            e.Property(x => x.DiscoveredBy).HasMaxLength(128).IsRequired();
            e.Property(x => x.DiscoveryContext).HasMaxLength(512).IsRequired().HasColumnName("discovery_context");
            e.Property(x => x.LifecycleStatus).HasMaxLength(32).IsRequired();
            e.Property(x => x.TypeDetailsJson);
            e.HasIndex(x => new { x.TargetId, x.CanonicalKey }).IsUnique();
            e.HasOne(x => x.Target)
                .WithMany()
                .HasForeignKey(x => x.TargetId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<BusJournalEntry>(e =>
        {
            e.ToTable("bus_journal");
            e.HasKey(x => x.Id);
            e.Property(x => x.Id).UseIdentityAlwaysColumn();
            e.Property(x => x.Direction).HasMaxLength(16).IsRequired();
            e.Property(x => x.MessageType).HasMaxLength(256).IsRequired();
            e.Property(x => x.ConsumerType).HasMaxLength(512);
            e.Property(x => x.PayloadJson).IsRequired();
            e.Property(x => x.HostName).HasMaxLength(256).IsRequired().HasColumnName("host_name");
            e.HasIndex(x => x.OccurredAtUtc);
        });

        modelBuilder.Entity<WorkerSwitch>(e =>
        {
            e.ToTable("worker_switches");
            e.HasKey(x => x.WorkerKey);
            e.Property(x => x.WorkerKey).HasMaxLength(64);
        });

        modelBuilder.Entity<HighValueFinding>(e =>
        {
            e.ToTable("high_value_findings");
            e.HasKey(x => x.Id);
            e.Property(x => x.Id).HasColumnName("id");
            e.Property(x => x.TargetId).HasColumnName("target_id");
            e.Property(x => x.SourceAssetId).HasColumnName("source_asset_id");
            e.Property(x => x.FindingType).HasColumnName("finding_type").HasMaxLength(64).IsRequired();
            e.Property(x => x.Severity).HasColumnName("severity").HasMaxLength(32).IsRequired();
            e.Property(x => x.PatternName).HasColumnName("pattern_name").HasMaxLength(256).IsRequired();
            e.Property(x => x.Category).HasColumnName("category").HasMaxLength(128);
            e.Property(x => x.MatchedText).HasColumnName("matched_text");
            e.Property(x => x.SourceUrl).HasColumnName("source_url").HasMaxLength(4096).IsRequired();
            e.Property(x => x.WorkerName).HasColumnName("worker_name").HasMaxLength(128).IsRequired();
            e.Property(x => x.ImportanceScore).HasColumnName("importance_score");
            e.Property(x => x.DiscoveredAtUtc).HasColumnName("discovered_at_utc");
            e.HasIndex(x => x.TargetId);
            e.HasIndex(x => x.DiscoveredAtUtc);
            e.HasOne(x => x.Target)
                .WithMany()
                .HasForeignKey(x => x.TargetId)
                .OnDelete(DeleteBehavior.Cascade);
        });
    }
}
