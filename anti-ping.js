const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');

const config = {
  token: process.env.DISCORD_TOKEN,
  directorshipRoleId: process.env.DIRECTORSHIP_ROLE_ID || '1517686125177737228',
  exemptRoleId: process.env.EXEMPT_ROLE_ID || '1517688590442692779',
  cooldownMs: parseInt(process.env.COOLDOWN_MS || '5000'),
  autoDeleteSec: parseInt(process.env.AUTO_DELETE_SEC || '20'),
  embedColor: 0x01d3ff,
  // Get the direct GIF URL from Tenor: open the GIF page, copy the "Copy Link" URL, paste below
  gifUrl: 'https://media.tenor.com/7694799882666584177/discord-ping-off-no-ping-reply-ping.gif',
};

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildMembers,
  ],
});

// Cooldown map: userId -> timestamp
const cooldowns = new Map();

client.once('ready', () => {
  console.log(`[ANTI-PING] Logged in as ${client.user.tag}`);
  console.log(`[ANTI-PING] Directorship role: ${config.directorshipRoleId}`);
  console.log(`[ANTI-PING] Exempt role: ${config.exemptRoleId}`);
});

client.on('messageCreate', async (message) => {
  // Ignore bots
  if (message.author.bot) return;

  // Ignore users with the exempt role (OSRP staff team)
  if (message.member && message.member.roles.cache.has(config.exemptRoleId)) return;

  const guild = message.guild;
  if (!guild) return;

  let targetMember = null;

  // Check direct @mentions for the directorship role
  if (message.mentions.roles.has(config.directorshipRoleId)) {
    // Find the actual member who has the directorship role (the one being pinged)
    for (const [id, member] of message.mentions.members) {
      if (member.roles.cache.has(config.directorshipRoleId)) {
        targetMember = member;
        break;
      }
    }
  }

  // Check reply pings — if the reply target has the directorship role
  if (!targetMember && message.reference && message.reference.messageId) {
    try {
      const replied = await message.channel.messages.fetch(message.reference.messageId);
      if (replied.author && replied.member && replied.member.roles.cache.has(config.directorshipRoleId)) {
        targetMember = replied.member;
      }
    } catch {
      // Message might be deleted or inaccessible
    }
  }

  if (!targetMember) return;

  // Cooldown check to prevent spam
  const now = Date.now();
  const last = cooldowns.get(message.author.id);
  if (last && now - last < config.cooldownMs) return;
  cooldowns.set(message.author.id, now);

  // Clean up old cooldown entries periodically
  if (cooldowns.size > 1000) {
    for (const [userId, ts] of cooldowns) {
      if (now - ts > config.cooldownMs * 2) cooldowns.delete(userId);
    }
  }

  const isReplyPing = message.reference && message.reference.messageId;

  const description = isReplyPing
    ? `<@${message.author.id}>\nDo not @ mention members of the **Directorship Team.**\nPlease disable the @ on the reply feature when replying.`
    : `<@${message.author.id}>\nDo not @ mention members of the **Directorship Team.**\n@Mentioning directors is a violation of rule 4.`;

  const embed = new EmbedBuilder()
    .setTitle('Directorship Mention Reminder')
    .setDescription(description)
    .setColor(config.embedColor)
    .setImage(config.gifUrl);

  try {
    const reminder = await message.reply({ embeds: [embed] });
    setTimeout(() => {
      reminder.delete().catch(() => {});
    }, config.autoDeleteSec * 1000);
  } catch (err) {
    console.error('[ANTI-PING] Failed to send reminder:', err.message);
  }
});

client.login(config.token).catch((err) => {
  console.error('[ANTI-PING] Login failed:', err.message);
  process.exit(1);
});
