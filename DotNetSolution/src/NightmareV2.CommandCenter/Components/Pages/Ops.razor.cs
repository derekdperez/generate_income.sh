using System.Linq;
using Microsoft.AspNetCore.Components.QuickGrid;
using NightmareV2.CommandCenter.Components.DataGrid;
using NightmareV2.CommandCenter.Models;

namespace NightmareV2.CommandCenter.Components.Pages;

public partial class Ops
{
    private static readonly GridSort<AssetGridRowDto> SortAssetDiscoveryContext =
        GridSort<AssetGridRowDto>.ByAscending(static a => a.DiscoveryContext);

    private IQueryable<AssetGridRowDto> FilteredAssets =>
        _assets.AsQueryable().Where(a =>
            GridTextFilter.Matches(a.Kind, _filterAssets)
            || GridTextFilter.Matches(a.LifecycleStatus, _filterAssets)
            || GridTextFilter.Matches(a.RawValue, _filterAssets)
            || GridTextFilter.Matches(a.DiscoveredBy, _filterAssets)
            || GridTextFilter.Matches(a.DiscoveryContext, _filterAssets)
            || GridTextFilter.Matches(a.CanonicalKey, _filterAssets));
}
