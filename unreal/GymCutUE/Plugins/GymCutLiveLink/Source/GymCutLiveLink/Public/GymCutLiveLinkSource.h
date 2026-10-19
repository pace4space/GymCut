// GymCut Live Link source: receives the live athlete skeleton from GymCut over UDP.
//
// Packet (JSON, one per datagram):
//   { "subject": "GymCut", "bones": [names], "parents": [indices, -1 = root],
//     "tr": [[tx, ty, tz, qx, qy, qz, qw], ...] }      parent-local transforms, cm
// Static data (bones/parents) is re-pushed whenever the bone list changes.
#pragma once

#include <atomic>

#include "CoreMinimal.h"
#include "HAL/Runnable.h"
#include "ILiveLinkSource.h"
#include "LiveLinkSourceFactory.h"

#include "GymCutLiveLinkSource.generated.h"

class FSocket;
class FRunnableThread;
class ILiveLinkClient;

class GYMCUTLIVELINK_API FGymCutLiveLinkSource : public ILiveLinkSource, public FRunnable
{
public:
	explicit FGymCutLiveLinkSource(int32 InPort);
	virtual ~FGymCutLiveLinkSource() override;

	// ILiveLinkSource
	virtual void ReceiveClient(ILiveLinkClient* InClient, FGuid InSourceGuid) override;
	virtual bool IsSourceStillValid() const override;
	virtual bool RequestSourceShutdown() override;
	virtual FText GetSourceType() const override;
	virtual FText GetSourceMachineName() const override;
	virtual FText GetSourceStatus() const override;

	// FRunnable
	virtual uint32 Run() override;
	virtual void Stop() override { bStopping = true; }

	int32 GetPort() const { return Port; }
	int32 GetFramesReceived() const { return FramesReceived; }

private:
	void HandlePacket(const FString& Json);

	int32 Port = 54322;
	ILiveLinkClient* Client = nullptr;
	FGuid SourceGuid;
	FSocket* Socket = nullptr;
	FRunnableThread* Thread = nullptr;
	std::atomic<bool> bStopping{false};
	std::atomic<int32> FramesReceived{0};
	std::atomic<double> LastReceive{0.0};
	TArray<FName> StaticBones;
	FName StaticSubject;
	FString Error;
};

/** Adds "GymCut" to the Live Link panel's + Source menu (connection string = UDP port). */
UCLASS()
class GYMCUTLIVELINK_API UGymCutLiveLinkSourceFactory : public ULiveLinkSourceFactory
{
	GENERATED_BODY()

public:
	virtual FText GetSourceDisplayName() const override;
	virtual FText GetSourceTooltip() const override;
	virtual EMenuType GetMenuType() const override { return EMenuType::MenuEntry; }
	virtual TSharedPtr<ILiveLinkSource> CreateSource(const FString& ConnectionString) const override;
};
