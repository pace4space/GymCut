#include "GymCutLiveLinkLibrary.h"

#include "Features/IModularFeatures.h"
#include "GymCutLiveLinkSource.h"
#include "ILiveLinkClient.h"

namespace
{
	TWeakPtr<FGymCutLiveLinkSource> GSource;
	FGuid GSourceGuid;

	ILiveLinkClient* GetClient()
	{
		IModularFeatures& Features = IModularFeatures::Get();
		return Features.IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName)
			? &Features.GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName)
			: nullptr;
	}
}

bool UGymCutLiveLinkLibrary::StartGymCutSource(int32 Port)
{
	ILiveLinkClient* Client = GetClient();
	if (!Client)
	{
		return false;
	}
	if (TSharedPtr<FGymCutLiveLinkSource> Existing = GSource.Pin())
	{
		if (Existing->GetPort() == Port && Existing->IsSourceStillValid())
		{
			return true;
		}
		StopGymCutSource();
	}
	TSharedPtr<FGymCutLiveLinkSource> Source = MakeShared<FGymCutLiveLinkSource>(Port);
	if (!Source->IsSourceStillValid())
	{
		return false;
	}
	GSourceGuid = Client->AddSource(Source);
	GSource = Source;
	return GSourceGuid.IsValid();
}

void UGymCutLiveLinkLibrary::StopGymCutSource()
{
	if (ILiveLinkClient* Client = GetClient())
	{
		if (GSourceGuid.IsValid())
		{
			Client->RemoveSource(GSourceGuid);
		}
	}
	GSourceGuid.Invalidate();
	GSource.Reset();
}

FString UGymCutLiveLinkLibrary::GetGymCutSourceStatus()
{
	if (TSharedPtr<FGymCutLiveLinkSource> S = GSource.Pin())
	{
		return FString::Printf(TEXT("running port=%d frames=%d"), S->GetPort(), S->GetFramesReceived());
	}
	return TEXT("stopped");
}
