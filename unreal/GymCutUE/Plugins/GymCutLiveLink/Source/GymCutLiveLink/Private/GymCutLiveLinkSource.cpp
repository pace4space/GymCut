#include "GymCutLiveLinkSource.h"

#include "Common/UdpSocketBuilder.h"
#include "Dom/JsonObject.h"
#include "HAL/RunnableThread.h"
#include "ILiveLinkClient.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "LiveLinkTypes.h"
#include "Roles/LiveLinkAnimationRole.h"
#include "Roles/LiveLinkAnimationTypes.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

#define LOCTEXT_NAMESPACE "GymCutLiveLink"

FGymCutLiveLinkSource::FGymCutLiveLinkSource(int32 InPort)
	: Port(InPort)
{
	const FIPv4Endpoint Endpoint(FIPv4Address(127, 0, 0, 1), Port);
	Socket = FUdpSocketBuilder(TEXT("GymCutLiveLink"))
		.AsNonBlocking()
		.AsReusable()
		.BoundToEndpoint(Endpoint)
		.WithReceiveBufferSize(1 << 20)
		.Build();
	if (!Socket)
	{
		Error = FString::Printf(TEXT("cannot bind udp 127.0.0.1:%d"), Port);
		return;
	}
	Thread = FRunnableThread::Create(this, TEXT("GymCutLiveLinkReceiver"), 128 * 1024, TPri_AboveNormal);
}

FGymCutLiveLinkSource::~FGymCutLiveLinkSource()
{
	bStopping = true;
	if (Thread)
	{
		Thread->WaitForCompletion();
		delete Thread;
		Thread = nullptr;
	}
	if (Socket)
	{
		Socket->Close();
		ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Socket);
		Socket = nullptr;
	}
}

void FGymCutLiveLinkSource::ReceiveClient(ILiveLinkClient* InClient, FGuid InSourceGuid)
{
	Client = InClient;
	SourceGuid = InSourceGuid;
}

bool FGymCutLiveLinkSource::IsSourceStillValid() const
{
	return Socket != nullptr && !bStopping;
}

bool FGymCutLiveLinkSource::RequestSourceShutdown()
{
	bStopping = true;
	return true;
}

FText FGymCutLiveLinkSource::GetSourceType() const
{
	return LOCTEXT("SourceType", "GymCut");
}

FText FGymCutLiveLinkSource::GetSourceMachineName() const
{
	return FText::FromString(FString::Printf(TEXT("udp:%d"), Port));
}

FText FGymCutLiveLinkSource::GetSourceStatus() const
{
	if (!Error.IsEmpty())
	{
		return FText::FromString(Error);
	}
	const double Age = FPlatformTime::Seconds() - LastReceive.load();
	if (FramesReceived.load() == 0)
	{
		return LOCTEXT("Waiting", "Waiting for GymCut…");
	}
	return Age < 1.0 ? LOCTEXT("Receiving", "Receiving") : LOCTEXT("Idle", "Idle (no athlete)");
}

uint32 FGymCutLiveLinkSource::Run()
{
	TArray<uint8> Buffer;
	Buffer.SetNumUninitialized(65536);
	while (!bStopping)
	{
		if (!Socket->Wait(ESocketWaitConditions::WaitForRead, FTimespan::FromMilliseconds(100)))
		{
			continue;
		}
		int32 Read = 0;
		// drain: only the newest datagram matters for a live pose
		FString Latest;
		while (Socket->Recv(Buffer.GetData(), Buffer.Num() - 1, Read) && Read > 0)
		{
			Buffer[Read] = 0;
			Latest = FString(UTF8_TO_TCHAR(reinterpret_cast<const char*>(Buffer.GetData())));
		}
		if (!Latest.IsEmpty())
		{
			HandlePacket(Latest);
		}
	}
	return 0;
}

void FGymCutLiveLinkSource::HandlePacket(const FString& Json)
{
	if (!Client)
	{
		return;
	}
	TSharedPtr<FJsonObject> Obj;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
	if (!FJsonSerializer::Deserialize(Reader, Obj) || !Obj.IsValid())
	{
		return;
	}
	const TArray<TSharedPtr<FJsonValue>>* BonesJ = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* ParentsJ = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* TrJ = nullptr;
	if (!Obj->TryGetArrayField(TEXT("bones"), BonesJ) || !Obj->TryGetArrayField(TEXT("parents"), ParentsJ)
		|| !Obj->TryGetArrayField(TEXT("tr"), TrJ) || BonesJ->Num() != ParentsJ->Num() || BonesJ->Num() != TrJ->Num())
	{
		return;
	}
	FString SubjectStr = TEXT("GymCut");
	Obj->TryGetStringField(TEXT("subject"), SubjectStr);
	const FName Subject(*SubjectStr);
	const FLiveLinkSubjectKey Key(SourceGuid, Subject);

	TArray<FName> Bones;
	Bones.Reserve(BonesJ->Num());
	for (const TSharedPtr<FJsonValue>& V : *BonesJ)
	{
		Bones.Add(FName(*V->AsString()));
	}
	if (Bones != StaticBones || Subject != StaticSubject)
	{
		TArray<int32> Parents;
		for (const TSharedPtr<FJsonValue>& V : *ParentsJ)
		{
			Parents.Add(static_cast<int32>(V->AsNumber()));
		}
		FLiveLinkStaticDataStruct Static(FLiveLinkSkeletonStaticData::StaticStruct());
		FLiveLinkSkeletonStaticData* Skel = Static.Cast<FLiveLinkSkeletonStaticData>();
		Skel->SetBoneNames(Bones);
		Skel->SetBoneParents(Parents);
		Client->PushSubjectStaticData_AnyThread(Key, ULiveLinkAnimationRole::StaticClass(), MoveTemp(Static));
		StaticBones = Bones;
		StaticSubject = Subject;
	}

	FLiveLinkFrameDataStruct Frame(FLiveLinkAnimationFrameData::StaticStruct());
	FLiveLinkAnimationFrameData* Anim = Frame.Cast<FLiveLinkAnimationFrameData>();
	Anim->WorldTime = FLiveLinkWorldTime(FPlatformTime::Seconds());
	Anim->Transforms.Reserve(TrJ->Num());
	for (const TSharedPtr<FJsonValue>& V : *TrJ)
	{
		const TArray<TSharedPtr<FJsonValue>>& A = V->AsArray();
		if (A.Num() < 7)
		{
			Anim->Transforms.Add(FTransform::Identity);
			continue;
		}
		const FVector T(A[0]->AsNumber(), A[1]->AsNumber(), A[2]->AsNumber());
		FQuat Q(A[3]->AsNumber(), A[4]->AsNumber(), A[5]->AsNumber(), A[6]->AsNumber());
		Q.Normalize();
		Anim->Transforms.Add(FTransform(Q, T, FVector::OneVector));
	}
	Client->PushSubjectFrameData_AnyThread(Key, MoveTemp(Frame));
	++FramesReceived;
	LastReceive = FPlatformTime::Seconds();
}

FText UGymCutLiveLinkSourceFactory::GetSourceDisplayName() const
{
	return LOCTEXT("FactoryName", "GymCut");
}

FText UGymCutLiveLinkSourceFactory::GetSourceTooltip() const
{
	return LOCTEXT("FactoryTip", "Live 3D athlete from GymCut (UDP 127.0.0.1:54322). Start '3D in Unreal' on GymCut's Live page.");
}

TSharedPtr<ILiveLinkSource> UGymCutLiveLinkSourceFactory::CreateSource(const FString& ConnectionString) const
{
	const int32 Port = ConnectionString.IsNumeric() ? FCString::Atoi(*ConnectionString) : 54322;
	return MakeShared<FGymCutLiveLinkSource>(Port);
}

#undef LOCTEXT_NAMESPACE
