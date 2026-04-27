using System.Text.Json;
using MassTransit;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using NightmareV2.Domain.Entities;
using NightmareV2.Infrastructure.Data;

namespace NightmareV2.Infrastructure.Messaging;

/// <summary>
/// Records publishes to <c>bus_journal</c>. Must not throw into MassTransit; scopes must live until <see cref="DbContext.SaveChangesAsync"/> completes.
/// </summary>
public sealed class BusJournalPublishObserver(
    IServiceScopeFactory scopeFactory,
    ILogger<BusJournalPublishObserver> logger) : IPublishObserver
{
    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = false };

    public Task PrePublish<T>(PublishContext<T> context)
        where T : class =>
        Task.CompletedTask;

    public Task PostPublish<T>(PublishContext<T> context)
        where T : class =>
        WriteAsync("Publish", typeof(T).Name, Json(context.Message), context.CancellationToken);

    public Task PublishFault<T>(PublishContext<T> context, Exception exception)
        where T : class =>
        Task.CompletedTask;

    private async Task WriteAsync(string direction, string messageType, string payloadJson, CancellationToken ct)
    {
        try
        {
            await using var scope = scopeFactory.CreateAsyncScope();
            var db = scope.ServiceProvider.GetRequiredService<NightmareDbContext>();
            db.BusJournal.Add(
                new BusJournalEntry
                {
                    Direction = direction,
                    MessageType = messageType,
                    ConsumerType = null,
                    PayloadJson = Truncate(payloadJson, 24_000),
                    OccurredAtUtc = DateTimeOffset.UtcNow,
                });
            await db.SaveChangesAsync(ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Bus journal skipped for {Direction} {MessageType}", direction, messageType);
        }
    }

    private static string Json<T>(T message) where T : class =>
        JsonSerializer.Serialize(message, message.GetType(), JsonOpts);

    private static string Truncate(string s, int max) =>
        s.Length <= max ? s : s[..max] + "…";
}

/// <summary>
/// Records consumes to <c>bus_journal</c>. Same lifetime rules as <see cref="BusJournalPublishObserver"/>.
/// </summary>
public sealed class BusJournalConsumeObserver(
    IServiceScopeFactory scopeFactory,
    ILogger<BusJournalConsumeObserver> logger) : IConsumeObserver
{
    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = false };

    public Task ConsumeFault<T>(ConsumeContext<T> context, Exception exception)
        where T : class =>
        Task.CompletedTask;

    public Task ConsumeFault(ConsumeContext context, Exception exception) => Task.CompletedTask;

    public Task PostConsume<T>(ConsumeContext<T> context)
        where T : class =>
        WriteAsync("Consume", typeof(T).Name, context.Message!, typeof(T).FullName, context.CancellationToken);

    public Task PostConsume(ConsumeContext context) => Task.CompletedTask;

    public Task PreConsume<T>(ConsumeContext<T> context)
        where T : class =>
        Task.CompletedTask;

    public Task PreConsume(ConsumeContext context) => Task.CompletedTask;

    private async Task WriteAsync(string direction, string messageType, object message, string? consumerType, CancellationToken ct)
    {
        try
        {
            await using var scope = scopeFactory.CreateAsyncScope();
            var db = scope.ServiceProvider.GetRequiredService<NightmareDbContext>();
            db.BusJournal.Add(
                new BusJournalEntry
                {
                    Direction = direction,
                    MessageType = messageType,
                    ConsumerType = consumerType,
                    PayloadJson = Truncate(Json(message), 24_000),
                    OccurredAtUtc = DateTimeOffset.UtcNow,
                });
            await db.SaveChangesAsync(ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Bus journal skipped for {Direction} {MessageType}", direction, messageType);
        }
    }

    private static string Json(object message) =>
        JsonSerializer.Serialize(message, message.GetType(), JsonOpts);

    private static string Truncate(string s, int max) =>
        s.Length <= max ? s : s[..max] + "…";
}
