"""Leaderboard feature module."""

from bot import *
from reviews import _load_shared_review_store, _aggregate_target_stats, get_review_member_or_user

def get_leaderboard_liked(limit: int = 5):
    store = _load_shared_review_store()
    counts = {}
    for review in store["reviews"]:
        if int(review["rating"]) in (4, 5):
            target_id = int(review["target_id"])
            counts[target_id] = counts.get(target_id, 0) + 1

    return [
        {"target_id": target_id, "approved": count}
        for target_id, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]


def get_leaderboard_reviewed(limit: int = 5):
    store = _load_shared_review_store()
    counts = {}
    ratings = {}

    for review in store["reviews"]:
        target_id = int(review["target_id"])
        counts[target_id] = counts.get(target_id, 0) + 1
        ratings.setdefault(target_id, []).append(int(review["rating"]))

    rows = [
        {
            "target_id": target_id,
            "review_count": counts[target_id],
            "average_rating": sum(ratings[target_id]) / counts[target_id],
        }
        for target_id in counts
    ]

    return sorted(
        rows,
        key=lambda row: (
            -row["review_count"],
            -row["average_rating"],
            row["target_id"],
        ),
    )[:limit]


def get_leaderboard_disliked(limit: int = 5):
    store = _load_shared_review_store()
    counts = {}
    for review in store["reviews"]:
        if int(review["rating"]) in (1, 2):
            target_id = int(review["target_id"])
            counts[target_id] = counts.get(target_id, 0) + 1

    return [
        {"target_id": target_id, "disliked": count}
        for target_id, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]


def get_user_stats(user_id: int):
    return _aggregate_target_stats(
        _load_shared_review_store(),
        user_id,
    )


@tree.command(
    name="leaderboard",
    description="View the reputation leaderboard."
)
async def leaderboard(interaction: discord.Interaction):
    # Acknowledge the interaction immediately. GitHub/API work can take longer
    # than Discord's initial interaction response window.
    await interaction.response.defer()

    try:
        # The shared review store is the source used by the leaderboard.
        # Load it once instead of refreshing the SQLite DB and then making
        # three additional GitHub reads (one for each leaderboard section).
        store = await asyncio.to_thread(_load_shared_review_store)
        reviews = store.get("reviews", [])

        # Aggregate everything from the same snapshot so all three sections
        # are consistent with one another and do not repeatedly hit GitHub.
        stats_by_target = {}
        liked_counts = {}
        neutral_counts = {}
        disliked_counts = {}

        for review in reviews:
            target_id = int(review["target_id"])
            rating = int(review["rating"])

            stats = stats_by_target.setdefault(
                target_id,
                {"total": 0, "approved": 0, "rating_sum": 0},
            )
            stats["total"] += 1
            stats["rating_sum"] += rating

            if rating in (4, 5):
                stats["approved"] += 1
                liked_counts[target_id] = liked_counts.get(target_id, 0) + 1
            elif rating == 3:
                neutral_counts[target_id] = neutral_counts.get(target_id, 0) + 1
            elif rating in (1, 2):
                disliked_counts[target_id] = disliked_counts.get(target_id, 0) + 1

        liked = sorted(
            (
                {"target_id": target_id, "approved": count}
                for target_id, count in liked_counts.items()
            ),
            key=lambda row: (-row["approved"], row["target_id"]),
        )[:5]

        reviewed = sorted(
            (
                {
                    "target_id": target_id,
                    "review_count": values["total"],
                    "average_rating": (
                        values["rating_sum"] / values["total"]
                        if values["total"]
                        else 0
                    ),
                }
                for target_id, values in stats_by_target.items()
            ),
            key=lambda row: (
                -row["review_count"],
                -row["average_rating"],
                row["target_id"],
            ),
        )[:5]

        neutral = sorted(
            (
                {"target_id": target_id, "neutral": count}
                for target_id, count in neutral_counts.items()
            ),
            key=lambda row: (-row["neutral"], row["target_id"]),
        )[:5]

        disliked = sorted(
            (
                {"target_id": target_id, "disliked": count}
                for target_id, count in disliked_counts.items()
            ),
            key=lambda row: (-row["disliked"], row["target_id"]),
        )[:5]

        embed = discord.Embed(
            title="leaderboard",
            color=discord.Color.dark_grey()
        )

        # Resolve each leaderboard target only once.
        target_ids = []
        for rows in (liked, reviewed, neutral, disliked):
            for row in rows:
                target_id = row["target_id"]
                if target_id not in target_ids:
                    target_ids.append(target_id)

        members = {}
        if interaction.guild is not None:
            for target_id in target_ids:
                members[target_id] = await get_review_member_or_user(
                    interaction.guild,
                    target_id,
                )

        liked_text = "**top 5 most liked users**\n\n"
        if not liked:
            liked_text += "No reviews yet."
        else:
            for index, row in enumerate(liked, start=1):
                target_id = row["target_id"]
                member = members.get(target_id)
                name = member.mention if member else f"<@{target_id}>"
                username = member.name if member else "unknown"
                stats = stats_by_target.get(
                    target_id,
                    {"total": 0, "approved": 0},
                )
                approval = (
                    (stats["approved"] / stats["total"]) * 100
                    if stats["total"]
                    else 0
                )
                liked_text += (
                    f"{index} {name} ({username}) 🟢 **{row['approved']}** "
                    f"({approval:.2f}% approval)\n"
                )
        embed.add_field(
            name="Most Liked",
            value=liked_text,
            inline=False,
        )

        reviewed_text = "**top 5 most reviewed**\n\n"
        if not reviewed:
            reviewed_text += "No reviews yet."
        else:
            for index, row in enumerate(reviewed, start=1):
                target_id = row["target_id"]
                member = members.get(target_id)
                name = member.mention if member else f"<@{target_id}>"
                username = member.name if member else "unknown"
                reviewed_text += (
                    f"{index} {name} ({username}) 📝 **{row['review_count']} reviews** "
                    f"(⭐ {row['average_rating']:.1f} avg)\n"
                )
        embed.add_field(
            name="Most Reviewed",
            value=reviewed_text,
            inline=False,
        )

        neutral_text = "**top 5 neutral users**\n\n"
        if not neutral:
            neutral_text += "No 3-star reviews yet."
        else:
            for index, row in enumerate(neutral, start=1):
                target_id = row["target_id"]
                member = members.get(target_id)
                name = member.mention if member else f"<@{target_id}>"
                username = member.name if member else "unknown"
                neutral_text += (
                    f"{index} {name} ({username}) 🟠 "
                    f"**{row['neutral']} neutral reviews**\n"
                )
        embed.add_field(
            name="Neutral (3 Stars)",
            value=neutral_text,
            inline=False,
        )

        disliked_text = "**top 5 most disliked users**\n\n"
        if not disliked:
            disliked_text += "No negative reviews yet."
        else:
            for index, row in enumerate(disliked, start=1):
                target_id = row["target_id"]
                member = members.get(target_id)
                name = member.mention if member else f"<@{target_id}>"
                username = member.name if member else "unknown"
                stats = stats_by_target.get(
                    target_id,
                    {"total": 0, "approved": 0},
                )
                approval = (
                    (stats["approved"] / stats["total"]) * 100
                    if stats["total"]
                    else 0
                )
                disliked_text += (
                    f"{index} {name} ({username}) 🔴 **{row['disliked']} negative reviews** "
                    f"({approval:.2f}% approval)\n"
                )
        embed.add_field(
            name="Most Disliked",
            value=disliked_text,
            inline=False,
        )

        await interaction.followup.send(embed=embed)

    except Exception as error:
        await interaction.followup.send(
            "❌ I couldn't load the leaderboard right now. "
            "Please try again in a moment.",
            ephemeral=True,
        )
        print(
            f"Leaderboard command failed: {type(error).__name__}: {error}"
        )

