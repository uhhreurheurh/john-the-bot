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


def _build_leaderboard_rows(store):
    """Build complete leaderboard rows for pagination."""
    stats_by_target = {}
    liked_counts = {}
    neutral_counts = {}
    disliked_counts = {}

    for review in store.get("reviews", []):
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
    )

    reviewed = sorted(
        (
            {
                "target_id": target_id,
                "review_count": stats["total"],
                "average_rating": stats["rating_sum"] / stats["total"],
            }
            for target_id, stats in stats_by_target.items()
        ),
        key=lambda row: (
            -row["review_count"],
            -row["average_rating"],
            row["target_id"],
        ),
    )

    neutral = sorted(
        (
            {"target_id": target_id, "neutral": count}
            for target_id, count in neutral_counts.items()
        ),
        key=lambda row: (-row["neutral"], row["target_id"]),
    )

    disliked = sorted(
        (
            {"target_id": target_id, "disliked": count}
            for target_id, count in disliked_counts.items()
        ),
        key=lambda row: (-row["disliked"], row["target_id"]),
    )

    return stats_by_target, liked, reviewed, neutral, disliked


LEADERBOARD_PAGE_SIZE = 5


class LeaderboardPaginationView(discord.ui.View):
    def __init__(self, guild, stats_by_target, liked, reviewed, neutral, disliked):
        super().__init__(timeout=300)
        self.guild = guild
        self.stats_by_target = stats_by_target
        self.liked = liked
        self.reviewed = reviewed
        self.neutral = neutral
        self.disliked = disliked
        self.page = 0
        self.pages = [
            ("Positive", self.liked),
            ("Neutral", self.neutral),
            ("Negative", self.disliked),
            ("Most Reviewed", self.reviewed),
        ]
        self._refresh_buttons()

    def _refresh_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= len(self.pages) - 1

    async def make_embed(self):
        page_name, rows = self.pages[self.page]

        target_ids = [row["target_id"] for row in rows]
        members = {}

        if self.guild is not None:
            for target_id in target_ids:
                members[target_id] = await get_review_member_or_user(
                    self.guild,
                    target_id,
                )

        def label(target_id):
            member = members.get(target_id)
            return (
                member.mention if member else f"<@{target_id}>",
                member.name if member else "unknown",
            )

        embed = discord.Embed(
            title="leaderboard",
            description=f"**{page_name}** • Page **{self.page + 1}/{len(self.pages)}**",
            color=(
                discord.Color.green()
                if page_name == "Positive"
                else discord.Color.orange()
                if page_name == "Neutral"
                else discord.Color.red()
                if page_name == "Negative"
                else discord.Color.blurple()
            ),
        )

        if not rows:
            empty_text = {
                "Positive": "No positive reviews yet.",
                "Neutral": "No 3-star reviews yet.",
                "Negative": "No negative reviews yet.",
                "Most Reviewed": "No reviews yet.",
            }[page_name]
            embed.description += f"\n\n{empty_text}"
        else:
            text = ""

            for rank, row in enumerate(rows, start=1):
                target_id = row["target_id"]
                mention, username = label(target_id)

                if page_name == "Positive":
                    stats = self.stats_by_target.get(
                        target_id,
                        {"total": 0, "approved": 0},
                    )
                    approval = (
                        stats["approved"] / stats["total"] * 100
                        if stats["total"]
                        else 0
                    )
                    text += (
                        f"{rank} {mention} ({username}) "
                        f"🟢 **{row['approved']}** positive reviews "
                        f"({approval:.2f}% approval)\n"
                    )

                elif page_name == "Neutral":
                    text += (
                        f"{rank} {mention} ({username}) "
                        f"🟠 **{row['neutral']}** neutral reviews\n"
                    )

                elif page_name == "Negative":
                    stats = self.stats_by_target.get(
                        target_id,
                        {"total": 0, "approved": 0},
                    )
                    approval = (
                        stats["approved"] / stats["total"] * 100
                        if stats["total"]
                        else 0
                    )
                    text += (
                        f"{rank} {mention} ({username}) "
                        f"🔴 **{row['disliked']}** negative reviews "
                        f"({approval:.2f}% approval)\n"
                    )

                else:
                    text += (
                        f"{rank} {mention} ({username}) "
                        f"📝 **{row['review_count']} reviews** "
                        f"(⭐ {row['average_rating']:.1f} avg)\n"
                    )

            embed.description += f"\n\n{text}"

        embed.set_footer(text="Use Previous and Next to switch leaderboard pages.")
        return embed

    @discord.ui.button(
        label="Previous",
        style=discord.ButtonStyle.secondary,
    )
    async def previous_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.page > 0:
            self.page -= 1
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=await self.make_embed(),
            view=self,
        )

    @discord.ui.button(
        label="Next",
        style=discord.ButtonStyle.primary,
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if self.page < len(self.pages) - 1:
            self.page += 1
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=await self.make_embed(),
            view=self,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


async def send_prefix_leaderboard(message):
    try:
        store = await asyncio.to_thread(_load_shared_review_store)
        stats_by_target, liked, reviewed, neutral, disliked = _build_leaderboard_rows(store)
        view = LeaderboardPaginationView(message.guild, stats_by_target, liked, reviewed, neutral, disliked)
        await message.reply(embed=await view.make_embed(), view=view, mention_author=False)
    except Exception as error:
        await message.reply("I couldn't load the leaderboard right now.", mention_author=False)
        print(f"Prefix leaderboard failed: {type(error).__name__}: {error}")


@tree.command(
    name="leaderboard",
    description="View the reputation leaderboard."
)
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        store = await asyncio.to_thread(_load_shared_review_store)
        stats_by_target, liked, reviewed, neutral, disliked = _build_leaderboard_rows(store)
        view = LeaderboardPaginationView(interaction.guild, stats_by_target, liked, reviewed, neutral, disliked)
        await interaction.followup.send(embed=await view.make_embed(), view=view)
    except Exception as error:
        await interaction.followup.send(
            "❌ I couldn't load the leaderboard right now. Please try again in a moment.",
            ephemeral=False,
        )
        print(f"Leaderboard command failed: {type(error).__name__}: {error}")
